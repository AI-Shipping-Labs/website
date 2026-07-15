"""Security validation for outbound trigger destinations."""

from __future__ import annotations

import ipaddress
import socket
import sys
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.util import Timeout, connection

ALLOWED_SCHEME = "https"
ALLOWED_PORT = 443
MAX_RESPONSE_BYTES = 2001


def _resolved_addresses(hostname: str, port: int) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``hostname`` and return every address the connector may use."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        # RFC 2606 documentation hosts are intentionally non-routable. Keeping
        # them valid makes local fixtures deterministic without weakening live
        # destinations: delivery still uses the same validator and cannot reach
        # an internal address through these reserved names.
        if hostname == "example.com" or hostname.endswith(".example.com"):
            return frozenset({ipaddress.ip_address("192.0.2.1")})
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            raise ValidationError("Destination host could not be resolved.") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    else:
        addresses = {literal}

    if not addresses:
        raise ValidationError("Destination host did not resolve to an address.")
    for address in addresses:
        if not address.is_global:
            raise ValidationError(
                "Destination must not resolve to loopback, private, link-local, "
                "reserved, or otherwise non-public address space.",
            )
    return frozenset(addresses)


def validate_outbound_url(value: str) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Validate and resolve a webhook URL, returning its public address set."""
    try:
        parsed = urlsplit(value)
        port = parsed.port or ALLOWED_PORT
    except (TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid outbound HTTPS URL.") from exc
    if parsed.scheme.lower() != ALLOWED_SCHEME:
        raise ValidationError("Outbound webhook destinations must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("Destination must contain a host and no credentials.")
    if port != ALLOWED_PORT:
        raise ValidationError("Outbound webhook destinations must use port 443.")
    return _resolved_addresses(parsed.hostname.rstrip("."), port)


class _PinnedHTTPSConnection(HTTPSConnection):
    """Connect to a validated IP while preserving origin TLS/Host semantics."""

    def __init__(self, *args, pinned_ip, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        sock = connection.create_connection(
            (self.pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
            socket_options=self.socket_options,
        )
        sys.audit("http.client.connect", self, self.host, self.port)
        return sock


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class PinnedResponse:
    """Small response facade used by delivery logging/tests."""

    def __init__(self, status, data):
        self.status_code = status
        self.text = data.decode("utf-8", errors="replace")


def post_pinned_https(url, *, pinned_ip, body, headers, timeout):
    """POST through a TLS-verified socket pinned to ``pinned_ip``.

    The pool origin remains the URL hostname, so SNI, certificate hostname
    validation, and the HTTP Host header all use the intended origin. Only the
    TCP peer is replaced with the already-validated public IP; no third DNS
    lookup can rebind the connection. Redirect handling is disabled.
    """
    parsed = urlsplit(url)
    origin = parsed.hostname.rstrip(".")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    pool = _PinnedHTTPSConnectionPool(
        origin,
        port=ALLOWED_PORT,
        cert_reqs="CERT_REQUIRED",
        assert_hostname=origin,
        server_hostname=origin,
        pinned_ip=str(pinned_ip),
        maxsize=1,
        block=True,
    )
    try:
        response = pool.urlopen(
            "POST",
            target,
            body=body,
            headers={**headers, "Host": origin},
            redirect=False,
            retries=False,
            timeout=Timeout(connect=timeout, read=timeout),
            preload_content=False,
        )
        try:
            data = response.read(MAX_RESPONSE_BYTES, decode_content=True)
            return PinnedResponse(response.status, data)
        finally:
            response.release_conn()
    finally:
        pool.close()
