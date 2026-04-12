"""Canonical persisted config model for the live gateway.

This module sits between the CLI, the `/config` API, and `ProviderManager`.
Use the helper methods on `Config` instead of reading raw fields directly when
you need the effective merged view, because compatibility fields and provider
aliases are normalized there.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .provider_catalog import (
    AWS_SDK_ENV_PRIORITY,
    PROVIDER_ENV_DEFAULT,
    PROVIDER_ENV_PRIORITY,
    normalize_provider_id,
)


CONFIG_DIR = Path.home() / ".open_llm_auth"
CONFIG_FILE = CONFIG_DIR / "config.json"


AuthMode = Literal["api-key", "aws-sdk", "oauth", "token", "cli"]
CredentialType = Literal["api_key", "token", "oauth", "cli"]
ModelApi = Literal[
    "openai-completions",
    "openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "google-generative-ai",
    "github-copilot",
    "bedrock-converse-stream",
    "ollama",
    "claude-cli",
    "codex-cli",
]


class AuthProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = None
    provider: str
    type: CredentialType
    key: Optional[str] = None
    token: Optional[str] = None
    access: Optional[str] = None
    refresh: Optional[str] = None
    expires: Optional[int] = None
    email: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
    account_id: Optional[str] = Field(default=None, alias="accountId")
    gateway_id: Optional[str] = Field(default=None, alias="gatewayId")
    base_url: Optional[str] = Field(default=None, alias="baseUrl")

    def secret(self) -> Optional[str]:
        if self.type == "api_key":
            return _resolve_secret_input(self.key)
        if self.type == "token":
            return _resolve_secret_input(self.token)
        return _resolve_secret_input(self.access)

    def is_expired(self, now_ms: Optional[int] = None) -> bool:
        if self.expires is None:
            return False
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        return self.expires > 0 and now >= self.expires


class ModelDefinitionConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    name: str
    api: Optional[ModelApi] = None
    reasoning: bool = False
    input: List[Literal["text", "image"]] = Field(default_factory=lambda: ["text"])
    cost: Dict[str, float] = Field(default_factory=dict)
    context_window: Optional[int] = Field(default=None, alias="contextWindow")
    max_tokens: Optional[int] = Field(default=None, alias="maxTokens")
    headers: Optional[Dict[str, str]] = None


class ProviderConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    base_url: Optional[str] = Field(default=None, alias="baseUrl")
    api_key: Optional[str] = Field(default=None, alias="apiKey")
    auth: Optional[AuthMode] = None
    api: Optional[ModelApi] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    auth_header: Optional[bool] = Field(default=None, alias="authHeader")
    models: List[ModelDefinitionConfig] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    mode: Optional[Literal["merge", "replace"]] = "merge"
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)


class AuthConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    profiles: Dict[str, AuthProfile] = Field(default_factory=dict)
    order: Dict[str, List[str]] = Field(default_factory=dict)


class AccessTokenConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = None
    token: str
    scopes: List[str] = Field(default_factory=list)
    admin: bool = False
    enabled: bool = True


class AuthorizationConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    tokens: Dict[str, AccessTokenConfig] = Field(default_factory=dict)
    legacy_admin_compatibility: bool = Field(
        default=True, alias="legacyAdminCompatibility"
    )


class DurableStateConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enabled: bool = True
    db_path: str = Field(
        default=str(CONFIG_DIR / "runtime_state.sqlite3"),
        alias="dbPath",
    )
    idempotency_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        alias="idempotencyTtlSeconds",
    )
    pending_lease_seconds: int = Field(
        default=60,
        alias="pendingLeaseSeconds",
    )
    fail_closed: bool = Field(default=True, alias="failClosed")


class EgressPolicyConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enabled: bool = True
    mode: Literal["off", "observe", "enforce"] = "enforce"
    resolve_dns: bool = Field(default=True, alias="resolveDns")
    fail_closed: bool = Field(default=True, alias="failClosed")
    enforce_https: bool = Field(default=True, alias="enforceHttps")
    allow_local_providers: List[str] = Field(
        default_factory=lambda: ["agent_bridge", "agent", "ollama", "vllm", "litellm"],
        alias="allowLocalProviders",
    )
    deny_hosts: List[str] = Field(
        default_factory=lambda: ["metadata.google.internal"],
        alias="denyHosts",
    )
    deny_cidrs: List[str] = Field(
        default_factory=lambda: [
            "169.254.169.254/32",
            "169.254.170.2/32",
            "100.100.100.200/32",
            "fd00:ec2::254/128",
        ],
        alias="denyCidrs",
    )


class TaskContractConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    enabled: bool = True
    enforce: bool = False
    cache_ttl_seconds: int = Field(default=30, alias="cacheTtlSeconds")
    supported_versions: List[str] = Field(
        default_factory=lambda: ["1.0"],
        alias="supportedVersions",
    )
    allow_legacy_missing: bool = Field(default=True, alias="allowLegacyMissing")
    fail_closed: bool = Field(default=False, alias="failClosed")


class Config(BaseModel):
    """Top-level persisted gateway config.

    For future agents: this model intentionally carries compatibility overlap
    (`authProfiles` and nested `auth.profiles`, `providers` and
    `models.providers`). Use the helper methods below instead of reading raw
    fields directly when you need the effective merged view.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    auth_profiles: Dict[str, AuthProfile] = Field(
        default_factory=dict, alias="authProfiles"
    )
    auth_order: Dict[str, List[str]] = Field(default_factory=dict, alias="authOrder")
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig)
    durable_state: DurableStateConfig = Field(
        default_factory=DurableStateConfig,
        alias="durableState",
    )
    egress_policy: EgressPolicyConfig = Field(
        default_factory=EgressPolicyConfig,
        alias="egressPolicy",
    )
    task_contract: TaskContractConfig = Field(
        default_factory=TaskContractConfig,
        alias="taskContract",
    )
    default_model: Optional[str] = Field(
        default="kimi-coding/k2p5", alias="defaultModel"
    )
    server_token: Optional[str] = Field(default=None, alias="serverToken")

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            self.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
        )

    def all_auth_profiles(self) -> Dict[str, AuthProfile]:
        """Return the effective merged auth-profile map with normalized providers."""
        merged: Dict[str, AuthProfile] = dict(self.auth_profiles)
        merged.update(self.auth.profiles)

        # Backfill ids and normalize provider aliases.
        for profile_id, profile in list(merged.items()):
            merged[profile_id] = profile.model_copy(
                update={
                    "id": profile.id or profile_id,
                    "provider": normalize_provider_id(profile.provider),
                }
            )
        return merged

    def all_auth_order(self) -> Dict[str, List[str]]:
        """Return the effective per-provider profile order after compatibility merge."""
        merged: Dict[str, List[str]] = dict(self.auth_order)
        merged.update(self.auth.order)
        normalized: Dict[str, List[str]] = {}
        for provider, profile_ids in merged.items():
            normalized[normalize_provider_id(provider)] = [
                p.strip() for p in profile_ids if isinstance(p, str) and p.strip()
            ]
        return normalized

    def all_provider_configs(self) -> Dict[str, ProviderConfig]:
        """Return the merged provider config map after alias normalization."""
        merged: Dict[str, ProviderConfig] = {}
        for provider, cfg in self.providers.items():
            merged[normalize_provider_id(provider)] = cfg
        for provider, cfg in self.models.providers.items():
            merged[normalize_provider_id(provider)] = cfg
        return merged


def _resolve_secret_input(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    if text.startswith("${") and text.endswith("}") and len(text) > 3:
        env_key = text[2:-1].strip()
        if env_key:
            env_val = os.getenv(env_key, "").strip()
            return env_val or None

    if text.isupper() and "_" in text:
        env_val = os.getenv(text, "").strip()
        if env_val:
            return env_val

    return text


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ with setdefault."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'\"")
        if key:
            os.environ.setdefault(key, value)


def load_config(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> Config:
    """Load the persisted config, falling back to defaults on first run or parse failure.

    If *env_path* is provided, load that .env file into os.environ first.
    If *config_path* is provided, load from that file instead of the global default.
    """
    if env_path is not None and env_path.exists():
        _load_dotenv(env_path)

    target = config_path or CONFIG_FILE
    if not target.exists():
        return Config()

    try:
        raw = target.read_text(encoding="utf-8")
        return Config.model_validate_json(raw)
    except Exception:
        return Config()


def resolve_env_api_keys(provider_id: str) -> List[Tuple[str, str]]:
    """Return env-derived credentials in the provider-specific priority order."""
    provider = normalize_provider_id(provider_id)
    results = []

    for env_key in PROVIDER_ENV_PRIORITY.get(provider, []):
        value = os.getenv(env_key, "").strip()
        if value:
            results.append((value, f"env:{env_key}"))

    default_env = PROVIDER_ENV_DEFAULT.get(provider)
    if default_env:
        value = os.getenv(default_env, "").strip()
        if value:
            results.append((value, f"env:{default_env}"))

    fallback = os.getenv(f"{provider.upper().replace('-', '_')}_API_KEY", "").strip()
    if fallback:
        results.append((fallback, f"env:{provider.upper().replace('-', '_')}_API_KEY"))

    if provider in ("google", "gemini"):
        google_val = os.getenv("GOOGLE_API_KEY", "").strip()
        if google_val:
            results.append((google_val, "env:GOOGLE_API_KEY"))

    return results


def resolve_aws_credential_source() -> Optional[str]:
    bearer = os.getenv("AWS_BEARER_TOKEN_BEDROCK", "").strip()
    if bearer:
        return "AWS_BEARER_TOKEN_BEDROCK"

    access = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    if access and secret:
        return "AWS_ACCESS_KEY_ID"

    profile = os.getenv("AWS_PROFILE", "").strip()
    if profile:
        return "AWS_PROFILE"

    for key in AWS_SDK_ENV_PRIORITY:
        if os.getenv(key, "").strip():
            return key
    return None


def get_provider_key(provider_id: str) -> Optional[str]:
    cfg = load_config()
    provider = normalize_provider_id(provider_id)

    env_keys = resolve_env_api_keys(provider)
    if env_keys:
        return env_keys[0][0]

    provider_cfg = cfg.all_provider_configs().get(provider)
    resolved = _resolve_secret_input(provider_cfg.api_key if provider_cfg else None)
    if resolved:
        return resolved

    profiles = cfg.all_auth_profiles()
    for _, profile in profiles.items():
        if normalize_provider_id(profile.provider) != provider:
            continue
        secret = profile.secret()
        if secret and not profile.is_expired():
            return secret

    return None


def find_executable(name: str) -> Optional[Path]:
    import shutil

    path = shutil.which(name)
    return Path(path) if path else None


def extract_gemini_cli_oauth_client() -> Optional[Tuple[str, str]]:
    """Extract OAuth client credentials from installed Gemini CLI bundled sources."""
    import re

    gemini_bin = find_executable("gemini")
    if not gemini_bin:
        return None

    try:
        # Resolve symlink to real directory
        resolved_bin = gemini_bin.resolve()
        gemini_dir = resolved_bin.parent.parent

        # Look for oauth2.js in node_modules
        search_paths = [
            gemini_dir
            / "node_modules"
            / "@google"
            / "gemini-cli-core"
            / "dist"
            / "src"
            / "code_assist"
            / "oauth2.js",
            gemini_dir
            / "node_modules"
            / "@google"
            / "gemini-cli-core"
            / "dist"
            / "code_assist"
            / "oauth2.js",
            # If globally installed, might be elsewhere, but these are common
        ]

        content = None
        for p in search_paths:
            if p.exists():
                content = p.read_text(encoding="utf-8")
                break

        if not content:
            # Fallback scan in gemini_dir (depth-limited)
            for root, dirs, files in os.walk(gemini_dir):
                if "oauth2.js" in files:
                    content = (Path(root) / "oauth2.js").read_text(encoding="utf-8")
                    break
                if root.count(os.sep) - str(gemini_dir).count(os.sep) > 5:
                    del dirs[:]  # don't go too deep

        if not content:
            return None

        id_match = re.search(r"(\d+-[a-z0-9]+\.apps\.googleusercontent\.com)", content)
        secret_match = re.search(r"(GOCSPX-[A-Za-z0-9_-]+)", content)

        if id_match and secret_match:
            return id_match.group(1), secret_match.group(1)

    except Exception:
        pass

    return None
