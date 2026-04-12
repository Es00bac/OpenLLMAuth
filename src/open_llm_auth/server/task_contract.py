"""Compatibility checks for mutating OpenBulma task routes.

The universal task API can create, approve, retry, or cancel tasks against an
OpenBulma runtime. This module probes the runtime contract endpoint and decides
whether the gateway should allow those mutations, cache the result, or fail
closed according to `taskContract` config.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict

import httpx

from ..config import Config
from ..providers.openbulma import OpenBulmaProvider


GATEWAY_TASK_CONTRACT_VERSION = "1.0"
REQUIRED_MUTATING_OPERATIONS = {"create", "approve", "retry", "cancel"}
_CONTRACT_CACHE: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class TaskContractDecision:
    compatible: bool
    code: str
    details: Dict[str, Any]
    from_cache: bool = False
    checked_at_ms: int = 0
    expires_at_ms: int = 0


def reset_task_contract_cache() -> None:
    _CONTRACT_CACHE.clear()


async def evaluate_task_contract(
    *,
    provider: OpenBulmaProvider,
    cfg: Config,
) -> TaskContractDecision:
    """Probe the runtime contract endpoint and cache the compatibility decision."""
    contract_cfg = cfg.task_contract
    if not contract_cfg.enabled:
        return TaskContractDecision(
            compatible=True,
            code="contract_check_disabled",
            details={"enabled": False},
        )

    key = _cache_key(provider)
    now_ms = int(time.time() * 1000)
    cached = _CONTRACT_CACHE.get(key)
    if cached and int(cached.get("expires_at_ms") or 0) > now_ms:
        decision: TaskContractDecision = cached["decision"]
        return TaskContractDecision(
            compatible=decision.compatible,
            code=decision.code,
            details=decision.details,
            from_cache=True,
            checked_at_ms=int(cached.get("checked_at_ms") or 0),
            expires_at_ms=int(cached.get("expires_at_ms") or 0),
        )

    decision = await _fetch_and_validate(provider=provider, cfg=cfg)
    ttl_seconds = max(1, int(contract_cfg.cache_ttl_seconds))
    expires_at_ms = now_ms + (ttl_seconds * 1000)
    _CONTRACT_CACHE[key] = {
        "decision": decision,
        "checked_at_ms": now_ms,
        "expires_at_ms": expires_at_ms,
    }
    return TaskContractDecision(
        compatible=decision.compatible,
        code=decision.code,
        details=decision.details,
        from_cache=False,
        checked_at_ms=now_ms,
        expires_at_ms=expires_at_ms,
    )


async def get_task_contract_status(
    *,
    provider: OpenBulmaProvider,
    cfg: Config,
) -> Dict[str, Any]:
    """Return a JSON-ready status envelope for contract introspection."""
    decision = await evaluate_task_contract(provider=provider, cfg=cfg)
    return {
        "provider": provider.provider_id,
        "baseUrl": provider.base_url,
        "compatible": decision.compatible,
        "decisionCode": decision.code,
        "details": decision.details,
        "fromCache": decision.from_cache,
        "checkedAtMs": decision.checked_at_ms,
        "expiresAtMs": decision.expires_at_ms,
        "gatewayVersion": GATEWAY_TASK_CONTRACT_VERSION,
        "enforce": bool(cfg.task_contract.enforce),
        "supportedVersions": [
            str(version).strip()
            for version in cfg.task_contract.supported_versions
            if str(version).strip()
        ],
        "requiredMutatingOperations": sorted(REQUIRED_MUTATING_OPERATIONS),
    }


def _cache_key(provider: OpenBulmaProvider) -> str:
    return f"{provider.provider_id}|{provider.base_url}"


async def _fetch_and_validate(
    *,
    provider: OpenBulmaProvider,
    cfg: Config,
) -> TaskContractDecision:
    contract_cfg = cfg.task_contract
    payload: Dict[str, Any] = {}

    try:
        payload = await provider.get_task_contract()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404 and contract_cfg.allow_legacy_missing:
            return TaskContractDecision(
                compatible=True,
                code="legacy_missing_contract",
                details={"reason": "contract_endpoint_missing"},
            )
        if contract_cfg.fail_closed:
            return TaskContractDecision(
                compatible=False,
                code="contract_probe_failed",
                details={"reason": "http_error", "statusCode": exc.response.status_code},
            )
        return TaskContractDecision(
            compatible=True,
            code="contract_probe_failed_monitor",
            details={"reason": "http_error", "statusCode": exc.response.status_code},
        )
    except Exception as exc:  # noqa: BLE001
        if contract_cfg.fail_closed:
            return TaskContractDecision(
                compatible=False,
                code="contract_probe_failed",
                details={"reason": "probe_exception", "message": str(exc)},
            )
        return TaskContractDecision(
            compatible=True,
            code="contract_probe_failed_monitor",
            details={"reason": "probe_exception", "message": str(exc)},
        )

    provider_version = str(payload.get("contractVersion") or payload.get("version") or "").strip()
    supported_versions = {
        str(version).strip() for version in contract_cfg.supported_versions if str(version).strip()
    }
    if not provider_version:
        if contract_cfg.allow_legacy_missing:
            return TaskContractDecision(
                compatible=True,
                code="legacy_missing_contract_version",
                details={"payload": payload},
            )
        return TaskContractDecision(
            compatible=False,
            code="missing_contract_version",
            details={"payload": payload},
        )

    operations_raw = payload.get("supportedOperations")
    operations = {
        str(item).strip().lower()
        for item in operations_raw
        if isinstance(item, str) and item.strip()
    } if isinstance(operations_raw, list) else set()

    missing_operations = sorted(REQUIRED_MUTATING_OPERATIONS - operations) if operations else []
    if missing_operations:
        return TaskContractDecision(
            compatible=False,
            code="contract_missing_operations",
            details={
                "providerVersion": provider_version,
                "requiredOperations": sorted(REQUIRED_MUTATING_OPERATIONS),
                "missingOperations": missing_operations,
                "providedOperations": sorted(operations),
            },
        )

    if provider_version not in supported_versions:
        return TaskContractDecision(
            compatible=False,
            code="unsupported_contract_version",
            details={
                "gatewayVersion": GATEWAY_TASK_CONTRACT_VERSION,
                "providerVersion": provider_version,
                "supportedVersions": sorted(supported_versions),
            },
        )

    return TaskContractDecision(
        compatible=True,
        code="contract_compatible",
        details={
            "gatewayVersion": GATEWAY_TASK_CONTRACT_VERSION,
            "providerVersion": provider_version,
            "supportedVersions": sorted(supported_versions),
        },
    )
