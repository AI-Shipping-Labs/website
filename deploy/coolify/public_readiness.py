"""Public routing proof for both independently routed Coolify hostnames."""

from .errors import ReleaseError

DEV_PING_URL = "https://dev.aishippinglabs.com/ping"


def public_endpoints_ready(ping_reader, production_url, sha):
    endpoints = (("production", production_url), ("dev", DEV_PING_URL))
    for name, url in endpoints:
        try:
            actual = ping_reader(url)
        except ReleaseError:
            raise ReleaseError(f"public-ping-request-failed endpoint={name}") from None
        if actual != sha:
            return False
    return True
