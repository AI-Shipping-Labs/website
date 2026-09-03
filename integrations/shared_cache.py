"""Request-path memo for the shared ``django_q`` cache.

``CACHES['django_q']`` is ``DatabaseCache`` outside tests. Every GET is
SQL, so request-path helpers must not treat a cache read as free.

This module memoizes those reads in-process for ``LOCAL_TTL_SECONDS``.
That also covers a single request: N ``get_config()`` / banner /
redirect / nav calls share one backend GET per key. Writes and deletes
update the memo immediately so the saving process sees its own change
without waiting for the TTL. Other processes notice the shared-cache
write no later than the TTL bound.

Heartbeats and invalidation still live in ``DatabaseCache``. This is
not a second cache service.
"""

import time

SHARED_CACHE_ALIAS = 'django_q'
LOCAL_TTL_SECONDS = 5

_UNSET = object()
_memo = {}


def monotonic_time():
    """Clock used for the in-process TTL. Tests patch this."""
    return time.monotonic()


def reset_local_shared_cache_memo():
    """Drop THIS process's request-path memo without touching the backend."""
    _memo.clear()


def snapshot_local_shared_cache_memo():
    """Return a shallow copy of the in-process memo (for tests)."""
    return dict(_memo)


def restore_local_shared_cache_memo(snapshot):
    """Replace the in-process memo with ``snapshot`` (for tests)."""
    _memo.clear()
    _memo.update(snapshot)


def get_shared_cache(key, default=None):
    """Return ``caches['django_q'][key]``, memoized for ``LOCAL_TTL_SECONDS``."""
    now = monotonic_time()
    entry = _memo.get(key)
    if entry is not None:
        expires_at, value = entry
        if expires_at > now:
            return value
    from django.core.cache import caches  # noqa: PLC0415
    value = caches[SHARED_CACHE_ALIAS].get(key, default)
    _memo[key] = (now + LOCAL_TTL_SECONDS, value)
    return value


def set_shared_cache(key, value, timeout=_UNSET):
    """Write through to the shared backend and refresh the local memo."""
    from django.core.cache import caches  # noqa: PLC0415
    cache = caches[SHARED_CACHE_ALIAS]
    if timeout is _UNSET:
        cache.set(key, value)
    else:
        cache.set(key, value, timeout)
    _memo[key] = (monotonic_time() + LOCAL_TTL_SECONDS, value)


def delete_shared_cache(key):
    """Delete from the shared backend and drop the local memo entry."""
    from django.core.cache import caches  # noqa: PLC0415
    caches[SHARED_CACHE_ALIAS].delete(key)
    _memo.pop(key, None)
