"""Typer CLI for running and configuring the live gateway."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import typer
import uvicorn

from .auth.manager import ProviderManager
from .config import AuthProfile, load_config


app = typer.Typer(help="Open LLM Auth CLI")
auth_app = typer.Typer(help="Authentication profile management")
models_app = typer.Typer(help="Model and provider operations")

app.add_typer(auth_app, name="auth")
app.add_typer(models_app, name="models")


def _mask_secret(value: Optional[str]) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080, reload: bool = False):
    """Start the gateway server.

    The running app exposes both the OpenAI-compatible chat shim and the
    universal task API, so future agents should not treat this as a
    chat-completions-only process.
    """
    typer.echo(f"Starting server on {host}:{port}")
    uvicorn.run("open_llm_auth.main:app", host=host, port=port, reload=reload)


@auth_app.command("configure")
def configure(
    provider: str,
    key: str = typer.Option(..., prompt=True, hide_input=True),
    profile: str = typer.Option("default", help="Profile suffix (default -> provider:default)."),
):
    """Legacy alias: configure an API key profile for a provider."""
    _add_api_key_profile(provider=provider, key=key, profile=profile)


@auth_app.command("add-api-key")
def add_api_key(
    provider: str,
    key: str = typer.Option(..., prompt=True, hide_input=True),
    profile: str = typer.Option("default", help="Profile suffix (default -> provider:default)."),
):
    """Add or update an API key auth profile."""
    _add_api_key_profile(provider=provider, key=key, profile=profile)


@auth_app.command("add-token")
def add_token(
    provider: str,
    token: str = typer.Option(..., prompt=True, hide_input=True),
    profile: str = typer.Option("default", help="Profile suffix (default -> provider:default)."),
    expires: int = typer.Option(0, help="Optional expiry timestamp in ms since epoch."),
):
    """Add or update a token profile."""
    cfg = load_config()
    profile_id = f"{provider}:{profile}" if ":" not in profile else profile
    cfg.auth_profiles[profile_id] = AuthProfile(
        id=profile_id,
        provider=provider,
        type="token",
        token=token,
        expires=expires if expires > 0 else None,
    )
    cfg.save()
    typer.echo(f"Saved token profile {profile_id}")


@auth_app.command("add-oauth")
def add_oauth(
    provider: str,
    access: str = typer.Option(..., prompt=True, hide_input=True),
    refresh: str = typer.Option("", help="Optional OAuth refresh token."),
    profile: str = typer.Option("default", help="Profile suffix (default -> provider:default)."),
    expires: int = typer.Option(0, help="Optional expiry timestamp in ms since epoch."),
):
    """Add or update an OAuth profile (access/refresh token pair)."""
    cfg = load_config()
    profile_id = f"{provider}:{profile}" if ":" not in profile else profile
    cfg.auth_profiles[profile_id] = AuthProfile(
        id=profile_id,
        provider=provider,
        type="oauth",
        access=access,
        refresh=refresh or None,
        expires=expires if expires > 0 else None,
    )
    cfg.save()
    typer.echo(f"Saved OAuth profile {profile_id}")


@auth_app.command("login-openai-codex")
def login_openai_codex(
    profile: str = typer.Option("default", help="Profile suffix (default -> openai-codex:default)."),
):
    """Log in with ChatGPT Plus/Pro via OpenAI Codex OAuth (PKCE)."""
    import asyncio

    async def _run_oauth() -> None:
        from .auth._openai_codex_oauth import run_openai_codex_oauth

        creds = await run_openai_codex_oauth()
        if not creds:
            typer.echo("OAuth cancelled or failed.")
            raise typer.Exit(1)

        cfg = load_config()
        profile_id = f"openai-codex:{profile}" if ":" not in profile else profile
        cfg.auth_profiles[profile_id] = AuthProfile(
            id=profile_id,
            provider="openai-codex",
            type="oauth",
            access=creds["access"],
            refresh=creds["refresh"],
            expires=creds["expires"],
            account_id=creds.get("account_id"),
        )
        cfg.auth_order.setdefault("openai-codex", [])
        if profile_id not in cfg.auth_order["openai-codex"]:
            cfg.auth_order["openai-codex"].insert(0, profile_id)
        cfg.save()
        typer.echo(f"Saved OAuth profile {profile_id}")
        if creds.get("account_id"):
            typer.echo(f"Account ID: {creds['account_id']}")

    asyncio.run(_run_oauth())


@auth_app.command("login-github-copilot")
def login_github_copilot(
    profile: str = typer.Option("default", help="Profile suffix (default -> github-copilot:default)."),
):
    """Log in with GitHub Copilot via device code flow."""
    import asyncio

    async def _run_login() -> None:
        from .auth._github_copilot_auth import login_github_copilot

        result = await login_github_copilot()

        cfg = load_config()
        profile_id = f"github-copilot:{profile}" if ":" not in profile else profile
        cfg.auth_profiles[profile_id] = AuthProfile(
            id=profile_id,
            provider="github-copilot",
            type="oauth",
            access=result["access"],
            refresh=result["refresh"],
            expires=result["expires"],
            base_url=result.get("baseUrl"),
        )
        cfg.auth_order.setdefault("github-copilot", [])
        if profile_id not in cfg.auth_order["github-copilot"]:
            cfg.auth_order["github-copilot"].insert(0, profile_id)
        cfg.save()
        typer.echo(f"Saved Copilot profile {profile_id}")

    asyncio.run(_run_login())


@auth_app.command("setup-token")
def setup_token(
    token: str = typer.Option(..., prompt="Paste your Anthropic setup token (sk-ant-oat01-...)", hide_input=True),
    profile: str = typer.Option("setup-token", help="Profile suffix."),
):
    """Add an Anthropic setup-token (from `claude --setup-token`)."""
    token = token.strip()
    if not token.startswith("sk-ant-oat01-"):
        typer.echo("Error: Expected token starting with sk-ant-oat01-", err=True)
        raise typer.Exit(1)
    if len(token) < 80:
        typer.echo("Error: Token looks too short; paste the full setup-token", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    profile_id = f"anthropic:{profile}" if ":" not in profile else profile
    cfg.auth_profiles[profile_id] = AuthProfile(
        id=profile_id,
        provider="anthropic",
        type="oauth",
        access=token,
    )
    cfg.auth_order.setdefault("anthropic", [])
    if profile_id not in cfg.auth_order["anthropic"]:
        cfg.auth_order["anthropic"].insert(0, profile_id)
    cfg.save()
    typer.echo(f"Saved setup-token profile {profile_id}")


@auth_app.command("set-order")
def set_order(
    provider: str,
    profiles: List[str] = typer.Argument(..., help="Ordered list of profile IDs for this provider."),
):
    """Set provider-specific auth profile priority order."""
    cleaned = [p.strip() for p in profiles if p.strip()]
    if not cleaned:
        raise typer.BadParameter("At least one profile ID is required.")

    cfg = load_config()
    cfg.auth_order[provider] = cleaned
    cfg.save()
    typer.echo(f"Set auth order for {provider}: {', '.join(cleaned)}")


@auth_app.command("set-server-token")
def set_server_token(
    token: str = typer.Option(..., prompt=True, hide_input=True, help="Token required for local API access."),
):
    """Set the token required to access this router's local API."""
    cfg = load_config()
    cfg.server_token = token.strip()
    cfg.save()
    typer.echo("Server token saved.")


@auth_app.command("list")
def list_profiles(show_secrets: bool = typer.Option(False, "--show-secrets", help="Print raw stored secrets.")):
    """List configured auth profiles and provider order."""
    cfg = load_config()
    profiles = cfg.all_auth_profiles()
    if not profiles:
        typer.echo("No auth profiles configured.")
    else:
        for profile_id in sorted(profiles.keys()):
            profile = profiles[profile_id]
            secret = profile.secret()
            display_secret = secret if show_secrets else _mask_secret(secret)
            typer.echo(
                f"{profile_id}: provider={profile.provider} type={profile.type} "
                f"expires={profile.expires or '-'} secret={display_secret}"
            )

    orders = cfg.all_auth_order()
    if orders:
        typer.echo("\nAuth order:")
        for provider, profile_ids in sorted(orders.items()):
            typer.echo(f"  {provider}: {', '.join(profile_ids)}")


@models_app.command("set-default")
def set_default_model(model: str):
    """Set default model used when provider/model is omitted."""
    cfg = load_config()
    cfg.default_model = model.strip()
    cfg.save()
    typer.echo(f"Default model set to {cfg.default_model}")


@models_app.command("list")
def list_models():
    """List provider/model references that the active manager can currently surface.

    This is intentionally filtered through real provider resolution, so the
    output reflects both catalog entries and currently-usable credentials.
    """

    async def _run() -> None:
        manager = ProviderManager()
        models = await manager.list_models()
        if not models:
            typer.echo("No models available.")
            return
        for model in models:
            typer.echo(model["id"])

    asyncio.run(_run())


@app.command("chat")
def chat(
    model: str = typer.Option("kimi-coding/k2p5", help="Model reference (provider/model)."),
    profile: Optional[str] = typer.Option(None, help="Optional auth profile id or suffix."),
    temperature: float = typer.Option(0.2, help="Sampling temperature."),
    max_tokens: int = typer.Option(4096, help="Maximum output tokens."),
    system: str = typer.Option("", help="Optional system prompt."),
):
    """Start a minimal terminal chat loop against the resolved provider/model."""

    async def _chat_once(
        manager: ProviderManager,
        *,
        model_ref: str,
        preferred_profile: Optional[str],
        messages: List[Dict[str, Any]],
        temp: float,
        max_out_tokens: int,
    ) -> str:
        resolved = manager.resolve(model_ref, preferred_profile=preferred_profile)
        payload = {
            "temperature": temp,
            "max_tokens": max_out_tokens,
        }
        response = await resolved.provider.chat_completion(
            model=resolved.model_id,
            messages=messages,
            payload=payload,
        )
        return _extract_assistant_text(response)

    manager = ProviderManager()
    messages: List[Dict[str, Any]] = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})

    typer.echo(f"Chat model: {model}")
    typer.echo("Type /exit to quit.")

    while True:
        try:
            user_text = typer.prompt("you").strip()
        except (KeyboardInterrupt, EOFError):
            typer.echo("\nbye")
            break

        if not user_text:
            continue
        if user_text.lower() in {"/exit", "/quit", "exit", "quit"}:
            typer.echo("bye")
            break

        messages.append({"role": "user", "content": user_text})

        try:
            assistant_text = asyncio.run(
                _chat_once(
                    manager,
                    model_ref=model,
                    preferred_profile=profile,
                    messages=messages,
                    temp=temperature,
                    max_out_tokens=max_tokens,
                )
            )
        except Exception as exc:
            typer.echo(f"error: {exc}")
            continue

        messages.append({"role": "assistant", "content": assistant_text})
        typer.echo(f"assistant: {assistant_text}")


def _add_api_key_profile(*, provider: str, key: str, profile: str) -> None:
    cfg = load_config()
    profile_id = f"{provider}:{profile}" if ":" not in profile else profile
    cfg.auth_profiles[profile_id] = AuthProfile(
        id=profile_id,
        provider=provider,
        type="api_key",
        key=key,
    )
    cfg.save()
    typer.echo(f"Saved API key profile {profile_id}")


def _extract_assistant_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: List[str] = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    out.append(text)
        return "\n".join(out).strip()
    return str(content or "")


if __name__ == "__main__":
    app()
