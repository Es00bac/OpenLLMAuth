from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from ..auth.manager import ProviderManager
from .usage_store import get_usage_store

from .auth import verify_admin_token
from ..config import (
    CONFIG_DIR,
    CONFIG_FILE,
    AuthProfile,
    Config,
    ProviderConfig,
    load_config,
)
from ..provider_catalog import (
    BUILTIN_MODELS,
    BUILTIN_PROVIDERS,
    get_all_builtin_provider_ids,
    get_builtin_provider_models,
    normalize_provider_id,
)
from .usage_api import build_usage_overview, clamp_days, collect_provider_telemetry
from .egress_policy import (
    UnsafeDestinationError,
    unsafe_destination_detail,
    validate_outbound_base_url,
)

router = APIRouter(prefix="/config", dependencies=[Depends(verify_admin_token)])

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "key",
    "token",
    "access",
    "refresh",
    "servertoken",
    "apikey",
}


class AuthProfileInput(BaseModel):
    provider: str
    type: str
    key: Optional[str] = None
    token: Optional[str] = None
    access: Optional[str] = None
    refresh: Optional[str] = None
    expires: Optional[int] = None
    email: Optional[str] = None
    base_url: Optional[str] = None


class ProviderInput(BaseModel):
    base_url: Optional[str] = None
    api: Optional[str] = None
    auth: Optional[str] = None
    api_key: Optional[str] = None
    auth_header: bool = True
    headers: Dict[str, str] = {}


@router.get("")
async def get_config() -> Dict[str, Any]:
    """Get current configuration."""
    cfg = load_config()
    return _redact_sensitive(cfg.model_dump(by_alias=True))


@router.post("")
async def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Save configuration."""
    try:
        cfg = Config.model_validate(config)
        _validate_all_egress_destinations(cfg)
        cfg.save()
        return {"status": "ok"}
    except UnsafeDestinationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsafe_destination",
                "message": "Outbound destination blocked by policy.",
                "details": unsafe_destination_detail(exc),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/builtin-providers")
async def get_builtin_providers() -> Dict[str, Dict[str, Any]]:
    """Get list of built-in providers."""
    return BUILTIN_PROVIDERS


@router.get("/providers")
async def get_providers() -> Dict[str, Any]:
    """Get all configured providers (custom only)."""
    cfg = load_config()
    return _redact_sensitive({
        "providers": cfg.providers,
        "models_providers": cfg.models.providers,
    })


@router.put("/providers/{provider_id}")
async def save_provider(provider_id: str, data: ProviderInput) -> Dict[str, str]:
    """Save or update a provider configuration."""
    cfg = load_config()
    _validate_egress_destination(
        provider_id=provider_id,
        base_url=data.base_url,
        cfg=cfg,
        phase="config_write",
    )

    provider = ProviderConfig(
        base_url=data.base_url,
        api=data.api,
        auth=data.auth,
        api_key=data.api_key,
        auth_header=data.auth_header,
        headers=data.headers,
    )

    cfg.providers[provider_id] = provider
    cfg.save()

    return {"status": "ok", "provider": provider_id}


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str) -> Dict[str, str]:
    """Delete a custom provider configuration."""
    cfg = load_config()
    normalized_id = normalize_provider_id(provider_id)

    # Remove from providers
    if provider_id in cfg.providers:
        del cfg.providers[provider_id]

    # Also check normalized form
    keys_to_remove = [
        k for k in cfg.providers.keys() if normalize_provider_id(k) == normalized_id
    ]
    for k in keys_to_remove:
        del cfg.providers[k]

    cfg.save()

    return {"status": "ok", "deleted": provider_id}


@router.get("/auth-profiles")
async def get_auth_profiles() -> List[Dict[str, Any]]:
    """Get all authentication profiles."""
    cfg = load_config()
    profiles = cfg.all_auth_profiles()

    result = []
    for _, profile in profiles.items():
        data = profile.model_dump(by_alias=True)
        result.append(_redact_sensitive(data))

    return result


@router.put("/auth-profiles/{profile_id:path}")
async def save_auth_profile(profile_id: str, data: AuthProfileInput) -> Dict[str, str]:
    """Save or update an authentication profile."""
    cfg = load_config()
    _validate_egress_destination(
        provider_id=data.provider,
        base_url=data.base_url,
        cfg=cfg,
        phase="config_write",
    )

    profile = AuthProfile(
        id=profile_id,
        provider=data.provider,
        type=data.type,
        key=data.key,
        token=data.token,
        access=data.access,
        refresh=data.refresh,
        expires=data.expires,
        email=data.email,
        base_url=data.base_url,
    )

    cfg.auth_profiles[profile_id] = profile
    cfg.save()

    return {"status": "ok", "profile": profile_id}


@router.delete("/auth-profiles/{profile_id:path}")
async def delete_auth_profile(profile_id: str) -> Dict[str, str]:
    """Delete an authentication profile."""
    cfg = load_config()

    removed = False
    if profile_id in cfg.auth_profiles:
        del cfg.auth_profiles[profile_id]
        removed = True

    if profile_id in cfg.auth.profiles:
        del cfg.auth.profiles[profile_id]
        removed = True

    if not removed:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    cfg.save()

    return {"status": "ok", "deleted": profile_id}


@router.get("/config-file-path")
async def get_config_file_path() -> Dict[str, str]:
    """Get the path to the configuration file."""
    return {
        "config_dir": str(CONFIG_DIR),
        "config_file": str(CONFIG_FILE),
    }


def mask_secret(value: Optional[str]) -> str:
    """Mask a secret value for display."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _redact_sensitive(value: Any) -> Any:
    """Recursively redact known secret-bearing fields from API responses."""
    if isinstance(value, BaseModel):
        value = value.model_dump(by_alias=True)

    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, child in value.items():
            normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if normalized in _SENSITIVE_KEYS and child not in (None, ""):
                redacted[key] = _REDACTED
            else:
                redacted[key] = _redact_sensitive(child)
        return redacted

    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]

    return value


def _validate_egress_destination(
    *,
    provider_id: str,
    base_url: Optional[str],
    cfg: Config,
    phase: str,
) -> None:
    try:
        validate_outbound_base_url(
            provider_id=provider_id,
            base_url=base_url,
            policy=cfg.egress_policy,
            phase=phase,
        )
    except UnsafeDestinationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsafe_destination",
                "message": "Outbound destination blocked by policy.",
                "details": unsafe_destination_detail(exc),
            },
        )


def _validate_all_egress_destinations(cfg: Config) -> None:
    for provider_id, provider_cfg in cfg.providers.items():
        validate_outbound_base_url(
            provider_id=provider_id,
            base_url=provider_cfg.base_url,
            policy=cfg.egress_policy,
            phase="config_write",
        )
    for provider_id, provider_cfg in cfg.models.providers.items():
        validate_outbound_base_url(
            provider_id=provider_id,
            base_url=provider_cfg.base_url,
            policy=cfg.egress_policy,
            phase="config_write",
        )
    for profile in cfg.all_auth_profiles().values():
        validate_outbound_base_url(
            provider_id=profile.provider,
            base_url=profile.base_url,
            policy=cfg.egress_policy,
            phase="config_write",
        )


@router.get("/configured-providers")
async def get_configured_providers() -> List[Dict[str, Any]]:
    """Get providers that have auth profiles configured."""
    cfg = load_config()
    profiles = cfg.all_auth_profiles()

    # Get unique providers from auth profiles
    provider_ids = set()
    for profile in profiles.values():
        provider_ids.add(normalize_provider_id(profile.provider))

    # Build provider list with their configs
    result = []
    for pid in sorted(provider_ids):
        provider_info = {"id": pid}

        # Get builtin config if available
        builtin = BUILTIN_PROVIDERS.get(pid)
        if builtin:
            provider_info["baseUrl"] = builtin.get("baseUrl")
            provider_info["api"] = builtin.get("api")
            provider_info["auth"] = builtin.get("auth")
            provider_info["isBuiltin"] = True
        else:
            provider_info["isBuiltin"] = False

        # Get custom config if available
        custom = cfg.providers.get(pid) or cfg.models.providers.get(pid)
        if custom:
            provider_info["customConfig"] = {
                "baseUrl": custom.base_url,
                "api": custom.api,
                "auth": custom.auth,
            }

        # Count auth profiles
        profile_count = sum(
            1 for p in profiles.values() if normalize_provider_id(p.provider) == pid
        )
        provider_info["authProfileCount"] = profile_count

        result.append(provider_info)

    return result


@router.get("/providers/{provider_id}/models")
async def get_provider_models(provider_id: str) -> List[Dict[str, Any]]:
    """Get models available for a specific provider."""
    cfg = load_config()
    normalized = normalize_provider_id(provider_id)

    # Get builtin models
    models = get_builtin_provider_models(normalized)

    # Get custom models from provider config
    provider_cfg = cfg.providers.get(normalized) or cfg.models.providers.get(normalized)
    if provider_cfg and provider_cfg.models:
        # Add custom models, avoiding duplicates
        existing_ids = {m["id"] for m in models}
        for model in provider_cfg.models:
            if model.id not in existing_ids:
                models.append(model.model_dump(by_alias=True))

    return models


@router.get("/usage/summary")
async def get_usage_summary(days: int = 30) -> Dict[str, Any]:
    """Return high-level usage aggregates."""
    return get_usage_store().get_summary(days=clamp_days(days))


@router.get("/usage/chart")
async def get_usage_chart(days: int = 30) -> Dict[str, Any]:
    """Return time-series usage data for Chart.js."""
    return get_usage_store().get_chart_data(days=clamp_days(days))


@router.get("/usage/providers")
async def get_usage_providers(days: int = 30) -> List[Dict[str, Any]]:
    """Return per-provider usage breakdown."""
    return get_usage_store().get_provider_breakdown(days=clamp_days(days))


@router.get("/usage/models")
async def get_usage_models(days: int = 30) -> List[Dict[str, Any]]:
    """Return per-model usage breakdown."""
    return get_usage_store().get_breakdown("model", days=clamp_days(days))


@router.get("/usage/endpoints")
async def get_usage_endpoints(days: int = 30) -> List[Dict[str, Any]]:
    """Return per-endpoint usage breakdown."""
    return get_usage_store().get_breakdown("endpoint", days=clamp_days(days))


@router.get("/usage/sources")
async def get_usage_sources(days: int = 30) -> List[Dict[str, Any]]:
    """Return per-source usage breakdown."""
    return get_usage_store().get_breakdown("source", days=clamp_days(days))


@router.get("/usage/overview")
async def get_usage_overview(days: int = 30, recent_limit: int = 100) -> Dict[str, Any]:
    """Return the full usage snapshot for dashboard and client apps."""
    return build_usage_overview(days=days, recent_limit=recent_limit)


@router.get("/usage/provider-telemetry")
async def get_usage_provider_telemetry(days: int = 7) -> Dict[str, Any]:
    """Return provider telemetry support and latest observed rate-limit state."""
    clamped_days = clamp_days(days)
    return {
        "days": clamped_days,
        "providers": await collect_provider_telemetry(days=clamped_days),
    }


@router.get("/usage/recent")
async def get_usage_recent(limit: int = 100) -> List[Dict[str, Any]]:
    """Return recent raw usage records."""
    return get_usage_store().get_recent_records(limit=max(1, min(limit, 1000)))


@router.post("/credentials/{profile_id}/test")
async def test_credentials(profile_id: str) -> Dict[str, Any]:
    """Test connectivity for a specific auth profile."""
    cfg = load_config()
    profiles = cfg.all_auth_profiles()
    profile = profiles.get(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    manager = ProviderManager()
    model_ref = f"{normalize_provider_id(profile.provider)}/assistant"
    try:
        resolved = manager.resolve(model_ref, preferred_profile=profile_id)
    except ValueError as exc:
        return {"status": "error", "profile": profile_id, "detail": str(exc)}

    try:
        models = await resolved.provider.list_models()
        return {
            "status": "ok",
            "profile": profile_id,
            "provider": resolved.provider_id,
            "models_found": len(models),
        }
    except Exception as exc:
        return {
            "status": "error",
            "profile": profile_id,
            "provider": resolved.provider_id,
            "detail": str(exc),
        }


_PROFILES_DIR = CONFIG_DIR / "profiles"


def _list_profile_snapshots() -> List[Dict[str, Any]]:
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for path in sorted(_PROFILES_DIR.glob("*.json")):
        try:
            stat = path.stat()
            result.append(
                {
                    "name": path.stem,
                    "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "size": stat.st_size,
                }
            )
        except Exception:
            continue
    return result


@router.get("/profiles")
async def list_profiles() -> List[Dict[str, Any]]:
    """List saved configuration snapshots."""
    return _list_profile_snapshots()


@router.post("/profiles")
async def save_profile(data: Dict[str, Any]) -> Dict[str, str]:
    """Save the current active config as a named snapshot."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required")
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid profile name")

    cfg = load_config()
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROFILES_DIR / f"{name}.json"
    path.write_text(cfg.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    return {"status": "ok", "name": name}


@router.post("/profiles/{name}/activate")
async def activate_profile(name: str) -> Dict[str, str]:
    """Restore a named snapshot to the active config."""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid profile name")

    path = _PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")

    try:
        raw = path.read_text(encoding="utf-8")
        cfg = Config.model_validate_json(raw)
        _validate_all_egress_destinations(cfg)
        cfg.save()
    except UnsafeDestinationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsafe_destination",
                "message": "Outbound destination blocked by policy.",
                "details": unsafe_destination_detail(exc),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", "name": name}


@router.post("/profiles/import")
async def import_profile(data: Dict[str, Any]) -> Dict[str, str]:
    """Validate and import a JSON config snapshot."""
    name = str(data.get("name") or "").strip()
    content = data.get("content")
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required")
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid profile name")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    try:
        parsed = json.loads(content)
        cfg = Config.model_validate(parsed)
        _validate_all_egress_destinations(cfg)
    except UnsafeDestinationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsafe_destination",
                "message": "Outbound destination blocked by policy.",
                "details": unsafe_destination_detail(exc),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}")

    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROFILES_DIR / f"{name}.json"
    path.write_text(content, encoding="utf-8")
    return {"status": "ok", "name": name}


@router.get("/profiles/export/{name}")
async def export_profile(name: str) -> Dict[str, Any]:
    """Download a saved snapshot as JSON."""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid profile name")

    path = _PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")

    return {
        "name": name,
        "content": path.read_text(encoding="utf-8"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
