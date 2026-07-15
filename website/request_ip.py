"""Deployment-aware client IP resolution for abuse controls."""

import ipaddress

# Production is ECS behind one AWS ALB. In append mode the ALB-observed
# source is the rightmost X-Forwarded-For hop; anything to its left was
# supplied before the trusted hop and is attacker-controlled for this policy.
TRUSTED_PROXY_HOPS = 1


def _valid_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or '').strip()))
    except ValueError:
        return ''


def client_ip_from_request(request):
    """Resolve the ALB-observed client without trusting prepended XFF hops."""
    remote = _valid_ip(request.META.get('REMOTE_ADDR'))
    if not remote:
        return ''
    remote_address = ipaddress.ip_address(remote)
    if remote_address.is_private or remote_address.is_loopback:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')
        if len(forwarded) >= TRUSTED_PROXY_HOPS:
            # Do not search leftward when the trusted position is malformed:
            # those earlier values are outside the explicit one-hop policy.
            candidate = forwarded[-TRUSTED_PROXY_HOPS]
            return _valid_ip(candidate) or remote
    return remote
