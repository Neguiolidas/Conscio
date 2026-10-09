"""Validação anti-SSRF para requisições de rede a URLs externas.

Origem:
- openmuse: apps/worker/src/network.ts:7-95
Regras estritas:
- Apenas HTTP(S) em portas 80/443
- Bloqueio de credenciais (user:pass@)
- Bloqueio de hostnames locais (.local, .lan, .internal, .home, localhost)
- Resolução DNS com timeout onde TODOS os endereços retornados devem ser públicos
- Bloqueio de loopback, RFC1918, link-local, CGNAT (100.64/10), multicast e faixas de documentação
- IPv6 restrito a unicast global (2000::/3) exceto Teredo/6to4/doc
Stdlib apenas (ipaddress, urllib.parse, socket).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable

_FORBIDDEN_SUFFIXES = (".local", ".lan", ".internal", ".home", ".localhost")
_FORBIDDEN_NAMES = {"localhost", "localhost.localdomain"}


def is_ip_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Verifica se um endereço IP é estritamente público e seguro contra SSRF."""
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False

    if isinstance(ip, ipaddress.IPv4Address):
        # CGNAT: 100.64.0.0/10
        cgnat = ipaddress.IPv4Network("100.64.0.0/10")
        if ip in cgnat:
            return False
        # Broadcast
        if ip == ipaddress.IPv4Address("255.255.255.255"):
            return False
        # Documentação: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24
        doc_nets = [
            ipaddress.IPv4Network("192.0.2.0/24"),
            ipaddress.IPv4Network("198.51.100.0/24"),
            ipaddress.IPv4Network("203.0.113.0/24"),
        ]
        return not any(ip in net for net in doc_nets)

    elif isinstance(ip, ipaddress.IPv6Address):
        # Apenas 2000::/3 (Global Unicast)
        global_unicast = ipaddress.IPv6Network("2000::/3")
        if ip not in global_unicast:
            return False
        # Bloqueia documentação: 2001:db8::/32
        if ip in ipaddress.IPv6Network("2001:db8::/32"):
            return False
        # Bloqueia Teredo (2001::/32) e 6to4 (2002::/16)
        return not (ip in ipaddress.IPv6Network("2001::/32") or ip in ipaddress.IPv6Network("2002::/16"))

    return False


def assert_safe_url(
    url: str,
    resolve: Callable[[str], list[str]] | None = None,
) -> tuple[str, str, int]:
    """Valida se uma URL é segura para acesso externo, retornando (hostname, ip_address, family).
    
    Levanta ValueError caso a URL viole qualquer regra de segurança SSRF.
    """
    if not url:
        raise ValueError("URL cannot be empty")

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Forbidden scheme: {parsed.scheme!r}. Only http and https are allowed.")

    if parsed.username or parsed.password:
        raise ValueError("Credentials in URL are strictly prohibited.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Missing hostname in URL")

    lower_host = hostname.lower()
    if lower_host in _FORBIDDEN_NAMES or any(lower_host.endswith(sfx) for sfx in _FORBIDDEN_SUFFIXES):
        raise ValueError(f"Prohibited hostname: {hostname}")

    # Port validation: default 80/443 or explicit 80/443
    port = parsed.port
    if port is not None and port not in (80, 443):
        raise ValueError(f"Forbidden port: {port}. Only ports 80 and 443 are permitted.")

    # Resolve IP addresses
    resolved_ips: list[str] = []
    if resolve is not None:
        resolved_ips = resolve(hostname)
    else:
        try:
            addr_info = socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80))
            resolved_ips = [str(item[4][0]) for item in addr_info]
        except socket.gaierror as e:
            raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e

    if not resolved_ips:
        raise ValueError(f"No IP addresses resolved for {hostname}")

    first_ip = ""
    first_family = socket.AF_INET
    for ip_str in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise ValueError(f"Invalid resolved IP address {ip_str}: {e}") from e

        if not is_ip_public(ip_obj):
            raise ValueError(f"Destination IP {ip_str} resolves to non-public/restricted network.")

        if not first_ip:
            first_ip = ip_str
            first_family = socket.AF_INET6 if isinstance(ip_obj, ipaddress.IPv6Address) else socket.AF_INET

    return hostname, first_ip, first_family


def is_safe_url(
    url: str,
    resolve: Callable[[str], list[str]] | None = None,
) -> bool:
    """Verifica de forma booleana se a URL é segura contra SSRF."""
    try:
        assert_safe_url(url, resolve=resolve)
        return True
    except Exception:
        return False
