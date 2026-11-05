"""SSRF guard for user-supplied external URLs (MED-506).

Resolves the URL's host and rejects it if any resolved address is not
publicly routable. Fails closed: invalid URLs, unresolvable hosts and
non-IP answers are all rejected.

Known limitation: DNS rebinding and redirects followed inside Browserless
must be handled by a network policy on the Browserless side.
"""

import ipaddress
import socket
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

Resolver = Callable[[str], Iterable[str]]


class UnsafeUrlError(ValueError):
    """Raised when a URL must not be fetched server-side."""


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def is_private_address(ip: str) -> bool:
    """True if the address is not safe to call from inside our network."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast


def assert_public_url(url: str, resolver: Optional[Resolver] = None) -> None:
    """Raise UnsafeUrlError unless every address the URL resolves to is public."""
    try:
        host = urlsplit(url).hostname
    except ValueError as exc:
        raise UnsafeUrlError("url is not a valid URL") from exc
    if not host:
        raise UnsafeUrlError("url is not a valid URL")

    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = list((resolver or _default_resolver)(host))
        except (OSError, UnicodeError) as exc:
            raise UnsafeUrlError("url host could not be resolved") from exc

    if not addresses or any(is_private_address(ip) for ip in addresses):
        raise UnsafeUrlError("url must not point to a private or internal address")
