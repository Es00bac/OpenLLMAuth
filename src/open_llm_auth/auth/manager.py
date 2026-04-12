"""Provider and credential resolution for the live gateway.

`ProviderManager` is the authority on provider activation and fallback. Catalog
presence alone does not mean a provider is usable; this module merges config,
profiles, env vars, refresh behavior, and egress checks before exposing a
provider instance to the HTTP routes or CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    AuthProfile,
    Config,
    ModelDefinitionConfig,
    ProviderConfig,
    load_config,
    resolve_aws_credential_source,
    resolve_env_api_keys,
)
from ..provider_catalog import (
    get_all_builtin_provider_ids,
    get_builtin_provider_config,
    get_builtin_provider_models,
    normalize_provider_id,
    resolve_cloudflare_ai_gateway_base_url,
)
from ..providers import (
    AnthropicCompatibleProvider,
    BaseProvider,
    BedrockConverseProvider,
    ClaudeCliProvider,
    CodexCliProvider,
    MinimaxProvider,
    OpenAICodexProvider,
    OpenAIProvider,
    AgentBridgeProvider,
)
from ..server.egress_policy import validate_outbound_base_url


@dataclass
class ResolvedModelRef:
    provider_id: str
    model_id: str
    profile_id: Optional[str] = None


@dataclass
class ResolvedProvider:
    provider: BaseProvider
    providers: List[BaseProvider]
    provider_id: str
    model_id: str
    profile_id: Optional[str]
    auth_source: str


class ProviderManager:
    """
    Resolve model references into concrete providers plus usable credentials.

    Agents should treat this class as the authority on activation rules:
    catalog presence alone does not mean a provider is reachable. Resolution
    here also enforces egress policy, profile ordering, env fallbacks, and some
    provider-specific OAuth refresh behavior.
    """
    def __init__(
        self,
        config_path: Optional[Path] = None,
        env_path: Optional[Path] = None,
    ) -> None:
        self._providers: Dict[str, BaseProvider] = {}
        self._config_path: Optional[Path] = config_path
        self._env_path: Optional[Path] = env_path
        self._config: Config = load_config(
            config_path=self._config_path,
            env_path=self._env_path,
        )

    def reload(self) -> None:
        self._config = load_config(
            config_path=self._config_path,
            env_path=self._env_path,
        )

    def resolve(
        self, model_ref: str, preferred_profile: Optional[str] = None
    ) -> ResolvedProvider:
        """Resolve a single model ref into the primary provider plus fallback chain."""
        self.reload()
        ref = self._resolve_model_ref(model_ref, preferred_profile=preferred_profile)
        provider_cfg = self._resolve_provider_config(ref.provider_id)
        if not provider_cfg:
            raise ValueError(f"Provider '{ref.provider_id}' is not configured.")

        profile_id = ref.profile_id
        keys_info = self._resolve_api_keys(
            provider_id=ref.provider_id,
            provider_cfg=provider_cfg,
            preferred_profile=profile_id,
        )

        providers_list = []
        selected_profile_id: Optional[str] = profile_id
        for api_key, auth_source, auth_mode in keys_info:
            candidate_profile_id = self._profile_id_from_auth_source(auth_source)
            effective_profile_id = candidate_profile_id or profile_id
            effective_base_url = self._resolve_effective_base_url(
                provider_id=ref.provider_id,
                provider_cfg=provider_cfg,
                profile_id=effective_profile_id,
            )
            cache_key = self._provider_cache_key(
                provider_id=ref.provider_id,
                profile_id=effective_profile_id,
                base_url=effective_base_url,
                auth_mode=auth_mode,
                api_key=api_key,
            )

            provider = self._providers.get(cache_key)
            if provider is None:
                provider = self._build_provider(
                    provider_id=ref.provider_id,
                    provider_cfg=provider_cfg,
                    api_key=api_key,
                    auth_mode=auth_mode,
                    profile_id=effective_profile_id,
                    effective_base_url=effective_base_url,
                )
                self._providers[cache_key] = provider
            providers_list.append(provider)
            if selected_profile_id is None and candidate_profile_id is not None:
                selected_profile_id = candidate_profile_id

        return ResolvedProvider(
            provider=providers_list[0],
            providers=providers_list,
            provider_id=ref.provider_id,
            model_id=ref.model_id,
            profile_id=selected_profile_id,
            auth_source=keys_info[0][1],
        )

    async def list_models(self) -> List[Dict[str, Any]]:
        """List models that survive both catalog lookup and current credential policy."""
        self.reload()
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for provider_id in self._all_provider_ids():
            provider_cfg = self._resolve_provider_config(provider_id)
            if not provider_cfg:
                continue

            # Skip providers without valid credentials
            try:
                keys_info = self._resolve_api_keys(
                    provider_id=provider_id,
                    provider_cfg=provider_cfg,
                    preferred_profile=None,
                )
                if not keys_info:
                    continue
            except ValueError:
                # No credentials available for this provider
                continue

            models = provider_cfg.models or []
            if not models:
                models = []
                for model in get_builtin_provider_models(provider_id):
                    try:
                        models.append(ModelDefinitionConfig.model_validate(model))
                    except Exception:
                        continue

            if self._should_discover_models(provider_id, models):
                try:
                    api_key, _, auth_mode = keys_info[0]
                    effective_base_url = self._resolve_effective_base_url(
                        provider_id=provider_id,
                        provider_cfg=provider_cfg,
                        profile_id=None,
                    )
                    provider = self._build_provider(
                        provider_id=provider_id,
                        provider_cfg=provider_cfg,
                        api_key=api_key,
                        auth_mode=auth_mode,
                        profile_id=None,
                        effective_base_url=effective_base_url,
                    )
                    remote_models = await provider.list_models()
                    models = self._merge_model_defs(models, remote_models)
                except Exception:
                    # Discovery failures should not break /v1/models.
                    logging.debug(
                        "Model discovery skipped for provider '%s'",
                        provider_id,
                        exc_info=True,
                    )

            for model in models:
                model_id = model.id.strip()
                if not model_id:
                    continue

                prefixed_id = f"{provider_id}/{model_id}"
                if prefixed_id in seen:
                    continue
                seen.add(prefixed_id)

                out.append(
                    {
                        "id": prefixed_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": provider_id,
                    }
                )

        return sorted(out, key=lambda item: item["id"])

    @staticmethod
    def _should_discover_models(
        provider_id: str, models: List[ModelDefinitionConfig]
    ) -> bool:
        if not models:
            return True
        provider = normalize_provider_id(provider_id)
        if provider == "openrouter":
            return all(m.id.strip() == "auto" for m in models if m.id)
        return provider in {"huggingface", "venice", "ollama", "vllm"}

    @staticmethod
    def _merge_model_defs(
        local: List[ModelDefinitionConfig],
        remote: List[Dict[str, Any]],
    ) -> List[ModelDefinitionConfig]:
        by_id: Dict[str, ModelDefinitionConfig] = {}
        for model in local:
            if model.id.strip():
                by_id[model.id.strip()] = model

        for item in remote:
            if not isinstance(item, dict):
                continue
            remote_id = str(item.get("id") or "").strip()
            if not remote_id:
                continue
            if remote_id in by_id:
                continue
            by_id[remote_id] = ModelDefinitionConfig(
                id=remote_id,
                name=remote_id,
            )
        return list(by_id.values())

    def _all_provider_ids(self) -> List[str]:
        # `agent_bridge` and `agent` are exposed by manager logic rather than the
        # builtin provider map because they bridge to a local runtime rather than
        # an upstream HTTP catalog entry.
        configured = set(self._config.all_provider_configs().keys())
        builtin = set(get_all_builtin_provider_ids()) | {
            "agent_bridge",
            "agent",
            "claude-cli",
            "codex-cli",
        }
        return sorted(configured | builtin)

    def _resolve_model_ref(
        self,
        raw_model: str,
        preferred_profile: Optional[str] = None,
    ) -> ResolvedModelRef:
        """Normalize user-facing model strings into `(provider, model, profile)`."""
        model = (raw_model or "").strip()
        if not model:
            if self._config.default_model:
                return self._resolve_model_ref(
                    self._config.default_model, preferred_profile
                )
            raise ValueError("Model is required.")

        if "/" in model:
            provider_segment, model_id = model.split("/", 1)
            model_id = model_id.strip()
            if not model_id:
                raise ValueError(f"Invalid model reference '{model}'.")

            profile_id: Optional[str] = (
                preferred_profile.strip() if preferred_profile else None
            )
            if ":" in provider_segment:
                provider_raw, profile_raw = provider_segment.split(":", 1)
                provider = normalize_provider_id(provider_raw)
                profile_clean = profile_raw.strip()
                if profile_clean:
                    if ":" in profile_clean:
                        profile_id = profile_clean
                    else:
                        profile_id = f"{provider}:{profile_clean}"
            else:
                provider = normalize_provider_id(provider_segment)

            return ResolvedModelRef(
                provider_id=provider, model_id=model_id, profile_id=profile_id
            )

        inferred_provider = self._infer_provider_for_model_id(model)
        if inferred_provider:
            return ResolvedModelRef(
                provider_id=inferred_provider,
                model_id=model,
                profile_id=preferred_profile,
            )

        if self._config.default_model and self._config.default_model != model:
            return self._resolve_model_ref(
                self._config.default_model, preferred_profile
            )

        raise ValueError(
            f"Model '{model}' does not include a provider prefix and could not be inferred. "
            "Use 'provider/model'."
        )

    def _infer_provider_for_model_id(self, model_id: str) -> Optional[str]:
        normalized_model = model_id.strip().lower()
        matches: List[str] = []

        for provider_id in self._all_provider_ids():
            provider_cfg = self._resolve_provider_config(provider_id)
            if not provider_cfg:
                continue

            ids = [m.id.strip().lower() for m in provider_cfg.models if m.id]
            if normalized_model in ids:
                matches.append(provider_id)

        if len(matches) == 1:
            return matches[0]

        # Heuristic fallback.
        if normalized_model.startswith("gpt-"):
            return "openai"
        if normalized_model.startswith("claude-"):
            return "anthropic"
        if "minimax" in normalized_model:
            return "minimax"
        if "kimi" in normalized_model:
            return "kimi"

        return None

    def _resolve_provider_config(self, provider_id: str) -> Optional[ProviderConfig]:
        """Merge builtin catalog data with local config for runtime use."""
        provider = normalize_provider_id(provider_id)

        builtin_raw = get_builtin_provider_config(provider)
        builtin_cfg = (
            ProviderConfig.model_validate(builtin_raw) if builtin_raw else None
        )

        configured_cfg = self._config.all_provider_configs().get(provider)
        mode = (self._config.models.mode or "merge").lower()
        if provider in {"agent_bridge", "agent"}:
            return configured_cfg or ProviderConfig(baseUrl="http://127.0.0.1:20100/v1")

        if (
            configured_cfg is None
            and builtin_cfg is None
            and provider_id not in {"agent_bridge", "agent"}
        ):
            return None

        if configured_cfg is None and provider_id not in {"agent_bridge", "agent"}:
            provider_cfg = builtin_cfg
        elif builtin_cfg is None and provider_id not in {"agent_bridge", "agent"}:
            provider_cfg = configured_cfg
        else:
            provider_cfg = builtin_cfg.model_copy(
                update={
                    "base_url": configured_cfg.base_url or builtin_cfg.base_url,
                    "api_key": configured_cfg.api_key or builtin_cfg.api_key,
                    "auth": configured_cfg.auth or builtin_cfg.auth,
                    "api": configured_cfg.api or builtin_cfg.api,
                    "headers": {**builtin_cfg.headers, **configured_cfg.headers},
                    "auth_header": (
                        configured_cfg.auth_header
                        if configured_cfg.auth_header is not None
                        else builtin_cfg.auth_header
                    ),
                    "models": self._merge_models(
                        builtin_cfg.models,
                        configured_cfg.models,
                        replace=(mode == "replace"),
                    ),
                }
            )

        if provider_cfg is None:
            return None

        # If catalog models are absent, seed from builtins.
        if not provider_cfg.models:
            for model in get_builtin_provider_models(provider):
                try:
                    provider_cfg.models.append(
                        ModelDefinitionConfig.model_validate(model)
                    )
                except Exception:
                    continue

        # Cloudflare AI Gateway base URL can be derived from profile metadata.
        if (
            provider == "cloudflare-ai-gateway"
            and not (provider_cfg.base_url or "").strip()
        ):
            derived = self._derive_cloudflare_base_url()
            if derived:
                provider_cfg = provider_cfg.model_copy(update={"base_url": derived})

        return provider_cfg

    @staticmethod
    def _merge_models(base: List[Any], override: List[Any], replace: bool) -> List[Any]:
        if replace:
            return list(override)
        if not base:
            return list(override)
        if not override:
            return list(base)

        out = {m.id: m for m in base if getattr(m, "id", "")}
        for model in override:
            model_id = getattr(model, "id", "")
            if not model_id:
                continue
            out[model_id] = model
        return list(out.values())

    def _derive_cloudflare_base_url(self) -> Optional[str]:
        for profile in self._profiles_for_provider("cloudflare-ai-gateway"):
            if profile.type != "api_key":
                continue
            account_id = (
                profile.account_id
                or profile.metadata.get("accountId")
                or profile.metadata.get("account_id")
                or ""
            )
            gateway_id = (
                profile.gateway_id
                or profile.metadata.get("gatewayId")
                or profile.metadata.get("gateway_id")
                or ""
            )
            base_url = resolve_cloudflare_ai_gateway_base_url(account_id, gateway_id)
            if base_url:
                return base_url
        return None

    def _profiles_for_provider(self, provider_id: str) -> List[AuthProfile]:
        provider = normalize_provider_id(provider_id)
        profiles = self._config.all_auth_profiles().values()
        return [p for p in profiles if normalize_provider_id(p.provider) == provider]

    def _resolve_api_keys(
        self,
        *,
        provider_id: str,
        provider_cfg: ProviderConfig,
        preferred_profile: Optional[str],
    ) -> List[Tuple[Optional[str], str, str]]:
        """Resolve credentials in retry/fallback order for a logical provider."""
        provider = normalize_provider_id(provider_id)
        auth_mode = provider_cfg.auth or (
            "aws-sdk" if provider == "amazon-bedrock" else "api-key"
        )
        explicit_provider_config = provider in self._config.all_provider_configs()

        profiles = self._config.all_auth_profiles()

        profile_candidates: List[str] = []
        if preferred_profile:
            preferred = preferred_profile.strip()
            if preferred:
                profile_candidates.append(preferred)
                if ":" not in preferred:
                    profile_candidates.append(f"{provider}:{preferred}")

        ordered = self._config.all_auth_order().get(provider, [])
        profile_candidates.extend(ordered)
        profile_candidates.extend(self._profile_ids_for_provider(provider))

        deduped_profile_candidates: List[str] = []
        seen: set[str] = set()
        for profile_id in profile_candidates:
            if profile_id in seen:
                continue
            seen.add(profile_id)
            deduped_profile_candidates.append(profile_id)

        results = []
        env_keys = resolve_env_api_keys(provider)
        if provider in {"google", "gemini"}:
            for env_key, env_source in env_keys:
                if env_key:
                    results.append(
                        (
                            env_key,
                            env_source or "env",
                            self._resolve_env_auth_mode(env_source, auth_mode),
                        )
                    )

        for profile_id in deduped_profile_candidates:
            profile = profiles.get(profile_id)
            if not profile:
                continue
            if normalize_provider_id(profile.provider) != provider:
                continue
            if profile.is_expired():
                refreshed = self._try_refresh_oauth(profile_id, profile)
                if not refreshed:
                    continue
                profile = refreshed
            secret = profile.secret()
            if secret:
                results.append(
                    (secret, f"profile:{profile_id}", self._profile_auth_mode(profile))
                )

        if provider not in {"google", "gemini"}:
            for env_key, env_source in env_keys:
                if env_key:
                    results.append(
                        (
                            env_key,
                            env_source or "env",
                            self._resolve_env_auth_mode(env_source, auth_mode),
                        )
                    )

        configured_key = self._resolve_secret_input(provider_cfg.api_key)
        if configured_key:
            results.append((configured_key, "models.providers", auth_mode))

        if not results:
            if provider_cfg.auth_header is False and explicit_provider_config:
                results.append((None, "provider-config:no-auth-header", auth_mode))
            elif auth_mode == "aws-sdk":
                source = resolve_aws_credential_source()
                if source:
                    results.append((None, f"aws-sdk:{source}", auth_mode))
                elif explicit_provider_config:
                    results.append((None, "aws-sdk:default-chain", auth_mode))
            elif auth_mode == "cli" or provider in {"claude-cli", "codex-cli"}:
                # CLI providers handle their own authentication
                results.append((None, "cli:self-authenticated", "cli"))

        if not results:
            raise ValueError(
                f"No credentials available for provider '{provider}'. "
                f"Set environment variables, auth profile, or models.providers.{provider}.apiKey."
            )

        deduped_results = []
        seen_keys = set()
        for key, source, mode in results:
            cache_val = key if key else "<none>"
            if cache_val not in seen_keys:
                seen_keys.add(cache_val)
                deduped_results.append((key, source, mode))

        return deduped_results

    def _try_refresh_oauth(
        self, profile_id: str, profile: AuthProfile
    ) -> Optional[AuthProfile]:
        """Attempt to refresh an expired profile. Returns updated profile or None."""
        provider = normalize_provider_id(profile.provider)

        # GitHub Copilot: re-exchange GitHub token for fresh Copilot token
        if provider == "github-copilot" and (profile.refresh or profile.token):
            return self._refresh_copilot_token(profile_id, profile)

        if provider == "anthropic":
            return self._refresh_anthropic_from_claude_cli(profile_id, profile)

        if profile.type != "oauth" or not profile.refresh:
            return None

        if provider == "openai-codex":
            try:
                import asyncio
                from .oauth_refresh import refresh_openai_codex_token

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        refreshed = pool.submit(
                            asyncio.run, refresh_openai_codex_token(profile.refresh)
                        ).result(timeout=30)
                else:
                    refreshed = asyncio.run(refresh_openai_codex_token(profile.refresh))

                profile.access = refreshed.access
                profile.refresh = refreshed.refresh
                profile.expires = refreshed.expires
                if refreshed.account_id:
                    profile.metadata["accountId"] = refreshed.account_id
                    profile.account_id = refreshed.account_id

                self._config.auth_profiles[profile_id] = profile
                self._config.save()
                logging.info("Refreshed OAuth token for profile %s", profile_id)
                return profile
            except Exception as exc:
                logging.warning(
                    "Failed to refresh OAuth token for %s: %s", profile_id, exc
                )
                return None

        if provider == "qwen-portal":
            return self._run_refresh(
                profile_id, profile, "oauth_refresh", "refresh_qwen_portal_token"
            )

        if provider == "minimax-portal":
            # MiniMax: try re-reading from ~/.minimax/oauth_creds.json
            return self._refresh_from_cli_creds(
                profile_id, profile, ".minimax/oauth_creds.json"
            )

        return None

    def _run_refresh(
        self,
        profile_id: str,
        profile: AuthProfile,
        module_name: str,
        func_name: str,
    ) -> Optional[AuthProfile]:
        """Generic OAuth token refresh using a function from the auth module."""
        try:
            import asyncio
            import importlib
            mod = importlib.import_module(f".{module_name}", package="open_llm_auth.auth")
            refresh_fn = getattr(mod, func_name)

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    refreshed = pool.submit(
                        asyncio.run, refresh_fn(profile.refresh)
                    ).result(timeout=30)
            else:
                refreshed = asyncio.run(refresh_fn(profile.refresh))

            profile.access = refreshed.access
            profile.refresh = refreshed.refresh
            profile.expires = refreshed.expires

            self._config.auth_profiles[profile_id] = profile
            self._config.save()
            logging.info("Refreshed OAuth token for profile %s", profile_id)
            return profile
        except Exception as exc:
            logging.warning(
                "Failed to refresh OAuth token for %s: %s", profile_id, exc
            )
            return None

    def _refresh_from_cli_creds(
        self,
        profile_id: str,
        profile: AuthProfile,
        cred_rel_path: str,
    ) -> Optional[AuthProfile]:
        """Re-read OAuth credentials from a CLI credential file."""
        import json as _json
        import pathlib

        cred_path = pathlib.Path.home() / cred_rel_path
        try:
            raw = _json.loads(cred_path.read_text())
        except Exception:
            return None

        access = raw.get("access_token")
        refresh = raw.get("refresh_token")
        expires = raw.get("expiry_date")

        if not access or not isinstance(access, str):
            return None
        if not expires or not isinstance(expires, (int, float)):
            return None

        import time
        now_ms = int(time.time() * 1000)
        if expires <= now_ms:
            return None

        profile.access = access
        profile.refresh = refresh or profile.refresh
        profile.expires = int(expires)

        self._config.auth_profiles[profile_id] = profile
        self._config.save()
        logging.info("Refreshed from CLI credentials for %s", profile_id)
        return profile

    def _refresh_anthropic_from_claude_cli(
        self, profile_id: str, profile: AuthProfile
    ) -> Optional[AuthProfile]:
        """Re-read Anthropic credentials from Claude credential files."""
        import time

        for cred_path in self._claude_credential_paths():
            raw = self._read_json_object(cred_path)
            if raw is None:
                continue

            parsed = self._extract_claude_oauth(raw)
            if parsed is None:
                continue

            access, refresh, expires_ms = parsed
            now_ms = int(time.time() * 1000)
            if expires_ms <= now_ms + 60_000:
                logging.warning(
                    "Claude CLI credentials from %s are expired or near expiry", cred_path
                )
                continue

            profile.access = access
            profile.refresh = refresh or profile.refresh
            profile.expires = int(expires_ms)

            self._config.auth_profiles[profile_id] = profile
            self._config.save()
            logging.info(
                "Refreshed Anthropic OAuth from Claude CLI credentials for %s using %s",
                profile_id,
                cred_path,
            )
            return profile

        logging.warning("Could not refresh Anthropic OAuth from Claude credential files")
        return None

    @staticmethod
    def _claude_credential_paths() -> List[Path]:
        override = os.getenv("CLAUDE_CREDENTIALS_PATH", "").strip()
        home = Path.home()
        candidates: List[Path] = []
        if override:
            candidates.append(Path(override).expanduser())
        candidates.extend(
            [
                home / ".claude" / ".credentials.json",
                home / ".claude" / "credentials.json",
                home / ".config" / "claude" / ".credentials.json",
                home / ".config" / "claude" / "credentials.json",
            ]
        )
        deduped: List[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    @staticmethod
    def _read_json_object(path: Path) -> Optional[Dict[str, Any]]:
        import json as _json

        try:
            raw = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(raw, dict):
            return raw
        return None

    @classmethod
    def _extract_claude_oauth(
        cls, raw: Dict[str, Any]
    ) -> Optional[Tuple[str, Optional[str], int]]:
        candidates: List[Dict[str, Any]] = []
        for key in (
            "claudeAiOauth",
            "claude_ai_oauth",
            "anthropicOauth",
            "anthropic_oauth",
            "oauth",
        ):
            value = raw.get(key)
            if isinstance(value, dict):
                candidates.append(value)

        for node in cls._iter_oauth_labeled_dict_nodes(raw, max_depth=5):
            candidates.append(node)

        seen: set[int] = set()
        for node in candidates:
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)

            access = cls._first_str(
                node,
                (
                    "accessToken",
                    "access_token",
                    "access",
                    "oauthToken",
                    "oauth_token",
                ),
            )
            if not access:
                continue

            expires_ms = cls._extract_expiry_ms(node)
            if expires_ms is None:
                continue

            refresh = cls._first_str(
                node,
                (
                    "refreshToken",
                    "refresh_token",
                    "refresh",
                ),
            )
            return access, refresh, expires_ms

        return None

    @staticmethod
    def _iter_oauth_labeled_dict_nodes(
        value: Any, *, max_depth: int
    ) -> List[Dict[str, Any]]:
        labels = ("oauth", "claude", "anthropic", "credential", "auth")
        out: List[Dict[str, Any]] = []
        stack: List[Tuple[str, Any, int]] = [("", value, 0)]
        while stack:
            key_name, current, depth = stack.pop()
            if depth > max_depth:
                continue
            if isinstance(current, dict):
                key_norm = key_name.lower()
                if any(label in key_norm for label in labels):
                    out.append(current)
                for child_key, child_value in current.items():
                    stack.append((str(child_key), child_value, depth + 1))
            elif isinstance(current, list):
                for child in current:
                    stack.append((key_name, child, depth + 1))
        return out

    @staticmethod
    def _first_str(node: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
        for key in keys:
            value = node.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    return cleaned
        return None

    @classmethod
    def _extract_expiry_ms(cls, node: Dict[str, Any]) -> Optional[int]:
        for key in (
            "expiresAt",
            "expires_at",
            "expires",
            "expiry",
            "expiryDate",
            "expiry_date",
            "expiresOn",
            "expires_on",
        ):
            parsed = cls._parse_timestamp_ms(node.get(key))
            if parsed is not None:
                return parsed

        for key in ("expiresIn", "expires_in", "expiresInSeconds"):
            raw = node.get(key)
            if isinstance(raw, str) and raw.strip().isdigit():
                raw = int(raw.strip())
            if isinstance(raw, (int, float)) and raw > 0:
                import time

                return int(time.time() * 1000) + int(raw * 1000)

        for key in ("expiresInMs", "expires_in_ms", "ttlMs", "ttl_ms"):
            raw = node.get(key)
            if isinstance(raw, str) and raw.strip().isdigit():
                raw = int(raw.strip())
            if isinstance(raw, (int, float)) and raw > 0:
                import time

                return int(time.time() * 1000) + int(raw)
        return None

    @staticmethod
    def _parse_timestamp_ms(value: Any) -> Optional[int]:
        if value is None:
            return None

        numeric: Optional[int] = None
        if isinstance(value, (int, float)):
            numeric = int(value)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.isdigit():
                numeric = int(text)
            else:
                try:
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    return None
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)

        if numeric is None or numeric <= 0:
            return None
        if numeric >= 10_000_000_000:
            return numeric
        return numeric * 1000

    def _refresh_copilot_token(
        self, profile_id: str, profile: AuthProfile
    ) -> Optional[AuthProfile]:
        """Re-exchange GitHub token for a fresh Copilot API token."""
        github_token = profile.refresh or profile.token
        if not github_token:
            return None
        try:
            import asyncio
            from ._github_copilot_auth import exchange_github_token_for_copilot

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, exchange_github_token_for_copilot(github_token)
                    ).result(timeout=30)
            else:
                result = asyncio.run(exchange_github_token_for_copilot(github_token))

            profile.access = result.copilot_token
            profile.expires = result.expires_at
            profile.base_url = result.base_url

            self._config.auth_profiles[profile_id] = profile
            self._config.save()
            logging.info("Refreshed Copilot token for profile %s", profile_id)
            return profile
        except Exception as exc:
            logging.warning("Failed to refresh Copilot token for %s: %s", profile_id, exc)
            return None

    def _copilot_base_url_from_profile(
        self, provider_id: str, profile_id: Optional[str] = None
    ) -> Optional[str]:
        """Get the Copilot API base URL from the auth profile."""
        if profile_id:
            profile = self._config.all_auth_profiles().get(profile_id)
            if profile and normalize_provider_id(profile.provider) == normalize_provider_id(
                provider_id
            ):
                if profile.base_url:
                    return profile.base_url
        for profile in self._profiles_for_provider(provider_id):
            if profile.base_url:
                return profile.base_url
        return None

    def _profile_ids_for_provider(self, provider_id: str) -> List[str]:
        provider = normalize_provider_id(provider_id)
        scored: List[Tuple[int, str]] = []
        for profile_id, profile in self._config.all_auth_profiles().items():
            if normalize_provider_id(profile.provider) == provider:
                mode = self._profile_auth_mode(profile)
                rank = 0 if mode == "oauth" else 1 if mode == "token" else 2
                scored.append((rank, profile_id))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [profile_id for _, profile_id in scored]

    @staticmethod
    def _profile_auth_mode(profile: AuthProfile) -> str:
        if profile.type == "api_key":
            return "api-key"
        if profile.type == "token":
            return "token"
        return "oauth"

    @staticmethod
    def _resolve_env_auth_mode(env_source: Optional[str], default_mode: str) -> str:
        if not env_source:
            return default_mode
        source = env_source.upper()
        if "OAUTH_TOKEN" in source:
            return "oauth"
        if source.endswith("_TOKEN") or "_TOKEN" in source:
            return "token"
        return default_mode

    @staticmethod
    def _resolve_secret_input(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        candidate = value.strip()
        if not candidate:
            return None

        if (
            candidate.startswith("${")
            and candidate.endswith("}")
            and len(candidate) > 3
        ):
            env_key = candidate[2:-1].strip()
            if env_key:
                env_value = os.getenv(env_key, "").strip()
                return env_value or None

        if candidate.isupper() and "_" in candidate:
            env_value = os.getenv(candidate, "").strip()
            if env_value:
                return env_value

        return candidate

    def _resolve_codex_account_id(
        self, provider_id: str, api_key: Optional[str]
    ) -> Optional[str]:
        """Get account_id from profile metadata or extract from JWT."""
        provider = normalize_provider_id(provider_id)
        for profile in self._profiles_for_provider(provider):
            account_id = (
                profile.account_id
                or profile.metadata.get("accountId")
                or profile.metadata.get("account_id")
            )
            if account_id:
                return account_id
        # Fallback: extract from JWT token
        if api_key:
            from ..providers.openai_codex import _extract_account_id

            return _extract_account_id(api_key)
        return None

    @staticmethod
    def _provider_cache_key(
        *,
        provider_id: str,
        profile_id: Optional[str],
        base_url: str,
        auth_mode: str,
        api_key: Optional[str],
    ) -> str:
        safe_key = api_key or "<none>"
        return f"{provider_id}|{profile_id or '-'}|{base_url}|{auth_mode}|{safe_key}"

    @staticmethod
    def _profile_id_from_auth_source(auth_source: str) -> Optional[str]:
        if auth_source.startswith("profile:"):
            profile_id = auth_source.split(":", 1)[1].strip()
            return profile_id or None
        return None

    def _resolve_effective_base_url(
        self,
        *,
        provider_id: str,
        provider_cfg: ProviderConfig,
        profile_id: Optional[str],
    ) -> str:
        base_url = (provider_cfg.base_url or "").strip().rstrip("/")
        if normalize_provider_id(provider_id) == "github-copilot":
            profile_base = self._copilot_base_url_from_profile(provider_id, profile_id)
            if profile_base:
                base_url = profile_base.strip().rstrip("/")
        return base_url

    def _build_provider(
        self,
        *,
        provider_id: str,
        provider_cfg: ProviderConfig,
        api_key: Optional[str],
        auth_mode: str,
        profile_id: Optional[str],
        effective_base_url: Optional[str] = None,
    ) -> BaseProvider:
        base_url = (
            (effective_base_url or "").strip().rstrip("/")
            if effective_base_url is not None
            else self._resolve_effective_base_url(
                provider_id=provider_id,
                provider_cfg=provider_cfg,
                profile_id=profile_id,
            )
        )

        # CLI providers don't need a base URL
        if not base_url and provider_id not in {"claude-cli", "codex-cli"}:
            raise ValueError(f"Provider '{provider_id}' has no baseUrl configured.")

        if base_url:
            validate_outbound_base_url(
                provider_id=provider_id,
                base_url=base_url,
                policy=self._config.egress_policy,
                phase="runtime",
            )

        api = provider_cfg.api or (
            "anthropic-messages"
            if provider_id in {"anthropic", "minimax"}
            else "openai-completions"
        )
        # GitHub Copilot: override base_url from profile and add Copilot headers
        if provider_id == "github-copilot":
            profile_base = self._copilot_base_url_from_profile(provider_id, profile_id)
            if profile_base:
                base_url = profile_base.rstrip("/")
            copilot_headers = {
                "User-Agent": "GitHubCopilotChat/0.35.0",
                "Editor-Version": "vscode/1.107.0",
                "Editor-Plugin-Version": "copilot-chat/0.35.0",
                "Copilot-Integration-Id": "vscode-chat",
                **provider_cfg.headers,
            }
            provider_cfg = provider_cfg.model_copy(update={"headers": copilot_headers})

        headers = self._build_headers(
            api=api,
            api_key=api_key,
            provider_headers=provider_cfg.headers,
            auth_mode=auth_mode,
            auth_header=provider_cfg.auth_header,
        )

        # Anthropic-style adapters expect base URLs without a trailing /v1.
        if api == "anthropic-messages" and base_url.endswith("/v1"):
            base_url = base_url[:-3]

        if api == "openai-codex-responses":
            account_id = self._resolve_codex_account_id(provider_id, api_key)
            return OpenAICodexProvider(
                provider_id=provider_id,
                api_key=api_key,
                base_url=base_url,
                headers=headers,
                account_id=account_id,
            )

        if provider_id == "claude-cli":
            return ClaudeCliProvider(
                provider_id=provider_id,
                api_key=None,
                base_url="",
                headers={},
            )

        if provider_id == "codex-cli":
            return CodexCliProvider(
                provider_id=provider_id,
                api_key=None,
                base_url="",
                headers={},
            )

        if provider_id == "minimax":
            if not api_key:
                raise ValueError("MiniMax requires an API key.")
            return MinimaxProvider(api_key=api_key, base_url=base_url, headers=headers)

        if provider_id == "agent_bridge" or provider_id == "agent":
            return AgentBridgeProvider(
                provider_id=provider_id,
                api_key=api_key,
                base_url=base_url or "http://127.0.0.1:20100/v1",
                headers=headers,
            )

        if api == "bedrock-converse-stream":
            return BedrockConverseProvider(
                provider_id=provider_id,
                api_key=api_key,
                base_url=base_url,
                headers=headers,
            )

        if api == "anthropic-messages":
            return AnthropicCompatibleProvider(
                provider_id=provider_id,
                api_key=api_key,
                base_url=base_url,
                headers=headers,
            )

        if api != "openai-completions":
            raise ValueError(
                f"Provider '{provider_id}' uses unsupported API adapter '{api}'. "
                "This router currently supports 'openai-completions', "
                "'anthropic-messages', 'openai-codex-responses', and "
                "'bedrock-converse-stream'."
            )

        return OpenAIProvider(
            provider_id=provider_id,
            api_key=api_key,
            base_url=base_url,
            headers=headers,
        )

    @staticmethod
    def _build_headers(
        *,
        api: str,
        api_key: Optional[str],
        provider_headers: Dict[str, str],
        auth_mode: str,
        auth_header: Optional[bool],
    ) -> Dict[str, str]:
        headers = {k: v for k, v in provider_headers.items()}
        lower_keys = {k.lower() for k in headers.keys()}

        if "content-type" not in lower_keys:
            headers["Content-Type"] = "application/json"

        allow_auth_header = True if auth_header is None else bool(auth_header)
        if not allow_auth_header:
            return headers

        if api == "anthropic-messages":
            if "anthropic-version" not in lower_keys:
                headers["anthropic-version"] = "2023-06-01"
            if auth_mode in {"oauth", "token"}:
                if api_key and "authorization" not in lower_keys:
                    headers["Authorization"] = f"Bearer {api_key}"
                # OAuth tokens (sk-ant-oat-*) require the oauth beta header
                if api_key and isinstance(api_key, str) and "sk-ant-oat" in api_key:
                    if "anthropic-beta" not in lower_keys:
                        headers["anthropic-beta"] = "oauth-2025-04-20,interleaved-thinking-2025-05-14"
            else:
                if api_key and "x-api-key" not in lower_keys:
                    headers["x-api-key"] = api_key
            return headers

        if (
            auth_mode in {"api-key", "token", "oauth"}
            and api_key
            and "authorization" not in lower_keys
        ):
            headers["Authorization"] = f"Bearer {api_key}"

        return headers


manager = ProviderManager()
