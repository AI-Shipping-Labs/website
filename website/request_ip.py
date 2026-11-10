"""Deployment-aware client IP resolution for abuse controls."""

import ipaddress

# Production is behind one Traefik proxy hop. The proxy appends the observed
# source to X-Forwarded-For; anything to its left is untrusted client input.
TRUSTED_PROXY_HOPS = 1


def _valid_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or '').strip()))
    except ValueError:
        return ''


def client_ip_from_request(request):
    """Resolve the trusted proxy's client without trusting prepended XFF hops."""
    return client_ip_from_addresses(
        request.META.get('REMOTE_ADDR'),
        request.META.get('HTTP_X_FORWARDED_FOR', ''),
    )


def client_ip_from_addresses(remote_address, forwarded_for=''):
    """Resolve one trusted proxy hop from a remote address and forwarded list."""
    remote = _valid_ip(remote_address)
    if not remote:
        return ''
    parsed_remote = ipaddress.ip_address(remote)
    if parsed_remote.is_private or parsed_remote.is_loopback:
        forwarded = str(forwarded_for or '').split(',')
        if len(forwarded) >= TRUSTED_PROXY_HOPS:
            # Do not search leftward when the trusted position is malformed:
            # those earlier values are outside the explicit one-hop policy.
            candidate = forwarded[-TRUSTED_PROXY_HOPS]
            return _valid_ip(candidate) or remote
    return remote


def client_network_for_logging(remote_address, forwarded_for=''):
    """Return a coarse IPv4 /24 or IPv6 /64 for request logs, never a raw IP."""
    address = client_ip_from_addresses(remote_address, forwarded_for)
    if not address:
        return 'unknown'
    parsed_address = ipaddress.ip_address(address)
    prefix = 24 if parsed_address.version == 4 else 64
    return str(ipaddress.ip_network(f'{address}/{prefix}', strict=False))
