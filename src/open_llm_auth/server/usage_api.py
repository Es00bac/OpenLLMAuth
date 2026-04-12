from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..auth.manager import ProviderManager
from ..provider_catalog import get_builtin_provider_models, normalize_provider_id
from .usage_store import UsageStore, get_usage_store


def clamp_days(days: int) -> int:
    return max(1, min(int(days), 365))


def build_usage_overview(
    *,
    days: int = 30,
    recent_limit: int = 100,
    store: Optional[UsageStore] = None,
) -> Dict[str, Any]:
    usage_store = store or get_usage_store()
    clamped_days = clamp_days(days)
    overview = usage_store.get_overview(days=clamped_days, recent_limit=max(1, min(int(recent_limit), 500)))
    overview["days"] = clamped_days
    return overview


async def collect_provider_telemetry(
    *,
    days: int = 7,
    manager: Optional[ProviderManager] = None,
    store: Optional[UsageStore] = None,
) -> List[Dict[str, Any]]:
    usage_store = store or get_usage_store()
    provider_manager = manager or ProviderManager()
    provider_manager.reload()
    clamped_days = clamp_days(days)

    latest_meta = usage_store.get_latest_provider_meta(days=clamped_days)
    latest_meta_by_provider: Dict[str, Dict[str, Any]] = {}
    for item in latest_meta:
        provider_id = normalize_provider_id(str(item.get("provider") or "unknown"))
        latest_meta_by_provider.setdefault(provider_id, item)

    provider_ids = _collect_provider_ids(provider_manager, usage_store, clamped_days)
    profiles = provider_manager._config.all_auth_profiles()
    configured_providers = provider_manager._config.all_provider_configs()
    items: List[Dict[str, Any]] = []

    for provider_id in provider_ids:
        provider_cfg = provider_manager._resolve_provider_config(provider_id)
        profile_ids = sorted(
            profile_id
            for profile_id, profile in profiles.items()
            if normalize_provider_id(getattr(profile, "provider", "")) == provider_id
        )
        local_models = []
        if provider_cfg is not None and getattr(provider_cfg, "models", None):
            local_models = [str(model.id) for model in provider_cfg.models if getattr(model, "id", None)]
        if not local_models:
            local_models = [str(model.get("id")) for model in get_builtin_provider_models(provider_id) if model.get("id")]

        item: Dict[str, Any] = {
            "provider": provider_id,
            "configured": provider_cfg is not None,
            "profile_ids": profile_ids,
            "profile_count": len(profile_ids),
            "model_ids": sorted(set(local_models)),
            "base_url": getattr(provider_cfg, "base_url", None) if provider_cfg is not None else None,
            "latest_observation": latest_meta_by_provider.get(provider_id),
            "telemetry": {
                "available": False,
                "provider": provider_id,
                "window_days": clamped_days,
                "kind": "provider_account",
                "note": "No provider-side usage telemetry available.",
            },
        }

        if provider_cfg is None:
            item["telemetry"]["note"] = "Provider is not configured."
            items.append(item)
            continue

        try:
            credentials = provider_manager._resolve_api_keys(
                provider_id=provider_id,
                provider_cfg=provider_cfg,
                preferred_profile=None,
            )
        except ValueError as exc:
            item["telemetry"] = {
                "available": False,
                "provider": provider_id,
                "window_days": clamped_days,
                "kind": "provider_account",
                "note": str(exc),
            }
            items.append(item)
            continue

        api_key, auth_source, auth_mode = credentials[0]
        effective_profile_id = provider_manager._profile_id_from_auth_source(auth_source)
        effective_base_url = provider_manager._resolve_effective_base_url(
            provider_id=provider_id,
            provider_cfg=provider_cfg,
            profile_id=effective_profile_id,
        )
        provider = provider_manager._build_provider(
            provider_id=provider_id,
            provider_cfg=provider_cfg,
            api_key=api_key,
            auth_mode=auth_mode,
            profile_id=effective_profile_id,
            effective_base_url=effective_base_url,
        )
        telemetry = await provider.get_usage_telemetry(days=clamped_days)
        item["telemetry"] = telemetry
        item["auth_source"] = auth_source
        items.append(item)

    return items


def _collect_provider_ids(
    manager: ProviderManager,
    usage_store: UsageStore,
    days: int,
) -> List[str]:
    provider_ids = set()
    for provider_id in manager._config.all_provider_configs().keys():
        provider_ids.add(normalize_provider_id(provider_id))
    for profile in manager._config.all_auth_profiles().values():
        provider_ids.add(normalize_provider_id(getattr(profile, "provider", "")))
    for item in usage_store.get_breakdown("provider", days=days):
        provider_ids.add(normalize_provider_id(str(item.get("provider") or "")))
    provider_ids.discard("")
    return sorted(provider_ids)
