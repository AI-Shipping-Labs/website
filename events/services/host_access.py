"""Signed, expiring, identity-bound access to one host's event controls."""

from datetime import timedelta
from urllib.parse import urlencode

from django.core import signing
from django.urls import reverse
from django.utils import timezone

from events.services.host_registration import resolve_host_user
from integrations.config import site_base_url

HOST_ACCESS_SALT = 'events.host-management.v1'
HOST_ACCESS_MIN_TTL_SECONDS = 30 * 24 * 60 * 60


class HostAccessError(Exception):
    """The supplied host-management credential cannot authorize access."""


class HostAccessExpired(HostAccessError):
    """The supplied credential is validly signed but too old."""


def generate_host_access_token(event, user):
    """Mint a token bound to the event, current host, and revocation version."""
    minimum_expiry = timezone.now() + timedelta(
        seconds=HOST_ACCESS_MIN_TTL_SECONDS,
    )
    event_expiry = event.effective_end_datetime + timedelta(days=7)
    expires_at = max(minimum_expiry, event_expiry)
    return signing.dumps(
        {
            'event_id': event.pk,
            'user_id': user.pk,
            'version': str(event.host_access_version),
            'action': 'manage_event_as_host',
            'expires_at': int(expires_at.timestamp()),
        },
        salt=HOST_ACCESS_SALT,
        compress=True,
    )


def validate_host_access_token(event, token):
    """Return the current host user when ``token`` is valid for ``event``."""
    try:
        payload = signing.loads(
            token,
            salt=HOST_ACCESS_SALT,
        )
    except signing.BadSignature as exc:
        raise HostAccessError from exc

    expires_at = payload.get('expires_at')
    if not isinstance(expires_at, int) or timezone.now().timestamp() > expires_at:
        raise HostAccessExpired

    host_user = resolve_host_user(event)
    if (
        payload.get('action') != 'manage_event_as_host'
        or payload.get('event_id') != event.pk
        or host_user is None
        or payload.get('user_id') != host_user.pk
        or payload.get('version') != str(event.host_access_version)
    ):
        raise HostAccessError
    return host_user


def build_host_access_url(event, user, *, anchor=''):
    """Build the absolute safe GET landing URL for host controls."""
    token = generate_host_access_token(event, user)
    path = reverse('event_host_manage', kwargs={'event_id': event.pk})
    url = f'{site_base_url()}{path}?{urlencode({"token": token})}'
    return f'{url}#{anchor}' if anchor else url
