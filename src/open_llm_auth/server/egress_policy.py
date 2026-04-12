from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlsplit

from ..config import EgressPolicyConfig
from ..provider_catalog import normalize_provider_id


_DEFAULT_DENY_CIDRS = (
    "169.254.169.254/32",
    "169.254.170.2/32",
    "100.100.100.200/32",
    "fd00:ec2::254/128",
)
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")
_BENCHMARK_V4 = ipaddress.ip_network("198.18.0.0/15")


@dataclass(frozen=True)
class DestinationDecision:
    provider_id: str
    scheme: str
    host: str
    reason_code: str
    phase: str
    resolved_ips: tuple[str, ...] = ()


class UnsafeDestinationError(ValueError):
    def __init__(
        self,
        *,
        provider_id: str,
        scheme: str,
        host: str,
        reason_code: str,
        phase: str,
        resolved_ips: Iterable[str] = (),
    ) -> None:
        self.decision = DestinationDecision(
            provider_id=provider_id,
            scheme=scheme,
            host=host,
            reason_code=reason_code,
            phase=phase,
            resolved_ips=tuple(resolved_ips),
        )
        super().__init__(
            f"Outbound destination blocked by policy ({reason_code}) for provider '{provider_id}'."
        )


def unsafe_destination_detail(exc: UnsafeDestinationError) -> dict[str, object]:
    decision = exc.decision
    detail: dict[str, object] = {
        "provider": decision.provider_id,
        "scheme": decision.scheme,
        "host": decision.host,
        "reason": decision.reason_code,
        "phase": decision.phase,
    }
    if decision.resolved_ips:
        detail["resolvedIps"] = list(decision.resolved_ips)
    return detail


def validate_outbound_base_url(
    *,
    provider_id: str,
    base_url: Optional[str],
    policy: EgressPolicyConfig,
    phase: str,
) -> None:
    raw = (base_url or "").strip()
    if not raw:
        return
    if not policy.enabled:
        return
    if (policy.mode or "enforce").lower() == "off":
        return

    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        _raise_unsafe(
            provider_id=provider_id,
            scheme=scheme or "unknown",
            host=_normalize_host(parsed.hostname) or "unknown",
            reason_code="unsupported_scheme",
            phase=phase,
        )

    if parsed.username or parsed.password:
        _raise_unsafe(
            provider_id=provider_id,
            scheme=scheme,
            host=_normalize_host(parsed.hostname) or "unknown",
            reason_code="userinfo_forbidden",
            phase=phase,
        )

    host = _normalize_host(parsed.hostname)
    if not host:
        _raise_unsafe(
            provider_id=provider_id,
            scheme=scheme,
            host="unknown",
            reason_code="missing_host",
            phase=phase,
        )

    normalized_provider = normalize_provider_id(provider_id)
    local_provider = _is_local_provider_allowed(normalized_provider, policy)
    allow_insecure_http = (
        not policy.enforce_https
        or local_provider
        or _env_truthy("OPEN_LLM_AUTH_ALLOW_INSECURE_EGRESS_HTTP")
    )
    if scheme == "http" and not allow_insecure_http:
        _raise_unsafe(
            provider_id=normalized_provider,
            scheme=scheme,
            host=host,
            reason_code="insecure_http_forbidden",
            phase=phase,
        )

    deny_hosts = _normalized_host_set(policy.deny_hosts, default=("metadata.google.internal",))
    if host in deny_hosts:
        _raise_unsafe(
            provider_id=normalized_provider,
            scheme=scheme,
            host=host,
            reason_code="denylisted_host",
            phase=phase,
        )

    if host in {"localhost", "localhost.localdomain"} and not local_provider:
        _raise_unsafe(
            provider_id=normalized_provider,
            scheme=scheme,
            host=host,
            reason_code="localhost_forbidden",
            phase=phase,
        )

    deny_networks = _parse_networks([*_DEFAULT_DENY_CIDRS, *policy.deny_cidrs])
    ip_literal = _parse_ip(host)
    if ip_literal is not None:
        blocked = _blocked_reason_for_ip(ip_literal, deny_networks)
        if blocked and not _allow_local_reason(local_provider, blocked):
            _raise_unsafe(
                provider_id=normalized_provider,
                scheme=scheme,
                host=host,
                reason_code=blocked,
                phase=phase,
            )
        return

    if not policy.resolve_dns:
        return

    try:
        resolved_ips = _resolve_host_ips(host, parsed.port)
    except OSError:
        if policy.fail_closed:
            _raise_unsafe(
                provider_id=normalized_provider,
                scheme=scheme,
                host=host,
                reason_code="dns_resolution_failed",
                phase=phase,
            )
        return

    if not resolved_ips:
        if policy.fail_closed:
            _raise_unsafe(
                provider_id=normalized_provider,
                scheme=scheme,
                host=host,
                reason_code="dns_no_answers",
                phase=phase,
            )
        return

    for addr in resolved_ips:
        blocked = _blocked_reason_for_ip(addr, deny_networks)
        if blocked and not _allow_local_reason(local_provider, blocked):
            _raise_unsafe(
                provider_id=normalized_provider,
                scheme=scheme,
                host=host,
                reason_code=f"resolved_{blocked}",
                phase=phase,
                resolved_ips=[str(ip) for ip in resolved_ips],
            )


def _raise_unsafe(
    *,
    provider_id: str,
    scheme: str,
    host: str,
    reason_code: str,
    phase: str,
    resolved_ips: Iterable[str] = (),
) -> None:
    raise UnsafeDestinationError(
        provider_id=provider_id,
        scheme=scheme,
        host=host,
        reason_code=reason_code,
        phase=phase,
        resolved_ips=resolved_ips,
    )


def _normalize_host(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = raw.rstrip(".")
    try:
        raw = raw.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return raw.lower()


def _normalized_host_set(
    values: Iterable[str],
    *,
    default: Iterable[str] = (),
) -> set[str]:
    output = {_normalize_host(v) for v in values if _normalize_host(v)}
    for item in default:
        normalized = _normalize_host(item)
        if normalized:
            output.add(normalized)
    return output


def _env_truthy(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _is_local_provider_allowed(provider_id: str, policy: EgressPolicyConfig) -> bool:
    normalized = normalize_provider_id(provider_id)
    allowed = {
        normalize_provider_id(item)
        for item in (policy.allow_local_providers or [])
        if (item or "").strip()
    }
    if _env_truthy("OPEN_LLM_AUTH_ALLOW_LOCALHOST_EGRESS"):
        return True
    return normalized in allowed


def _parse_networks(values: Iterable[str]) -> tuple[ipaddress._BaseNetwork, ...]:
    parsed = []
    for cidr in values:
        text = (cidr or "").strip()
        if not text:
            continue
        try:
            parsed.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return tuple(parsed)


def _parse_ip(text: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return _normalize_ip(ipaddress.ip_address(text))
    except ValueError:
        return None


def _normalize_ip(value: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
        return value.ipv4_mapped
    return value


def _allow_local_reason(local_provider: bool, reason_code: str) -> bool:
    if not local_provider:
        return False
    return reason_code in {
        "loopback_ip",
        "private_ip",
        "link_local_ip",
        "cgnat_ip",
    }


def _blocked_reason_for_ip(
    ip: ipaddress._BaseAddress,
    deny_networks: tuple[ipaddress._BaseNetwork, ...],
) -> Optional[str]:
    addr = _normalize_ip(ip)

    for network in deny_networks:
        if addr.version != network.version:
            continue
        if addr in network:
            return "denylisted_cidr"

    if isinstance(addr, ipaddress.IPv4Address):
        if addr in _CGNAT_V4:
            return "cgnat_ip"
        if addr in _BENCHMARK_V4:
            return "benchmark_ip"

    if addr.is_loopback:
        return "loopback_ip"
    if addr.is_link_local:
        return "link_local_ip"
    if addr.is_private:
        return "private_ip"
    if addr.is_multicast:
        return "multicast_ip"
    if addr.is_unspecified:
        return "unspecified_ip"
    if addr.is_reserved:
        return "reserved_ip"
    return None


def _resolve_host_ips(host: str, port: Optional[int]) -> tuple[ipaddress._BaseAddress, ...]:
    family = socket.AF_UNSPEC
    socktype = socket.SOCK_STREAM
    entries = socket.getaddrinfo(host, port or 443, family, socktype)
    parsed = []
    seen = set()
    for entry in entries:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        address = str(sockaddr[0])
        ip = _parse_ip(address)
        if ip is None:
            continue
        key = (ip.version, str(ip))
        if key in seen:
            continue
        seen.add(key)
        parsed.append(ip)
    return tuple(parsed)
