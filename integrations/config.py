"""Configuration helper for integration settings.

Provides get_config() which checks the database first, then falls back
to Django settings and environment variables. Web processes use an
in-process cache that is cleared when settings are saved via Studio.
Worker processes bypass that cache and read runtime config fresh.

Cross-process invalidation
==========================

In production we run three gunicorn workers plus a separate qcluster
process — four independent Python processes that each hold their own
copy of ``_cache``. ``clear_config_cache()`` only mutates the globals in
the process that handled the Studio save, so the other three keep
serving stale values until they restart. To make a save visible
everywhere we publish a short stamp into the shared ``caches['django_q']``
DatabaseCache (already on every host that talks to the application DB —
see ``website.settings`` CACHES). Each process records the stamp it saw
when it last read the DB; on every ``get_config()`` call we re-read the
shared stamp and, if it changed, repopulate the in-process cache from
the DB. That costs one cache GET per ``get_config()`` call and zero
extra DB queries when nothing has changed.

The stamp is intentionally opaque (a random uuid hex). We never compare
the value, only "is it the same string we recorded last time".
"""

import logging
import os
import sys
import uuid

from django.apps.registry import AppRegistryNotReady
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

_STAMP_CACHE_KEY = 'integration_settings_stamp'
_STAMP_CACHE_ALIAS = 'django_q'

logger = logging.getLogger(__name__)

_DB_CONFIG_EXCEPTIONS = (AppRegistryNotReady, ImproperlyConfigured, DatabaseError)
_CACHE_STAMP_EXCEPTIONS = (
    InvalidCacheBackendError,
    ImproperlyConfigured,
    DatabaseError,
)

_cache = {}
_cache_populated = False
_cache_stamp = None


def get_config(key, default='', *, use_settings=True):
    """Get config value: DB first, then env var, then Django settings, then default.

    Args:
        key: The setting key (e.g. 'ZOOM_CLIENT_ID').
        default: Default value if not found anywhere.

    Returns:
        str: The setting value.
    """
    global _cache, _cache_populated, _cache_stamp
    if running_in_worker_process():
        return _get_config_uncached(key, default, use_settings=use_settings)

    if not _cache_populated:
        _populate_cache()
    else:
        # The in-process cache thinks it's fresh — but another process
        # may have written via clear_config_cache() since we last read.
        # Compare the published stamp with the one we recorded during
        # _populate_cache() and repopulate if they differ.
        current_stamp = _read_stamp()
        if current_stamp is not None and current_stamp != _cache_stamp:
            _populate_cache()
    if key in _cache and _cache[key]:
        return _cache[key]
    # Check Django settings first (supports @override_settings in tests).
    # Guard with settings.configured so we don't accidentally trigger
    # LazySettings._setup() while website/settings.py is still being
    # imported — that would freeze a partial Settings snapshot and break
    # every later `settings.X` lookup. `settings.configured` is a plain
    # attribute that does NOT force setup, so it's safe to read first.
    from django.conf import settings  # noqa: PLC0415
    if use_settings and settings.configured:
        settings_val = getattr(settings, key, None)
        if settings_val is not None:
            return settings_val
    # Then env var
    env_val = os.environ.get(key)
    if env_val is not None:
        return env_val
    return default


def running_in_worker_process():
    """Return True when executing inside a Django-Q worker process."""
    if os.environ.get('DJANGO_QCLUSTER_PROCESS') == 'true':
        return True
    return any(arg == 'qcluster' for arg in sys.argv)


def _get_config_uncached(key, default='', *, use_settings=True):
    """Read one config key without touching the in-process DB cache."""
    try:
        from integrations.models import IntegrationSetting  # noqa: PLC0415
        db_value = (
            IntegrationSetting.objects
            .filter(key=key)
            .values_list('value', flat=True)
            .first()
        )
        if db_value:
            return db_value
    except _DB_CONFIG_EXCEPTIONS:
        logger.warning(
            'Unable to read integration config from database',
            exc_info=True,
            extra={'config_key': key},
        )

    from django.conf import settings  # noqa: PLC0415
    if use_settings and settings.configured:
        settings_val = getattr(settings, key, None)
        if settings_val is not None:
            return settings_val

    env_val = os.environ.get(key)
    if env_val is not None:
        return env_val
    return default


def site_base_url():
    """Resolved canonical site URL: DB override > env value > default."""
    from django.conf import settings  # noqa: PLC0415
    return get_config('SITE_BASE_URL', settings.SITE_BASE_URL)


def is_enabled(key):
    """Check if a config flag is enabled (handles both bool and string values).

    Args:
        key: The setting key (e.g. 'SLACK_ENABLED').

    Returns:
        bool: True if the value is truthy ('true', True, '1').
    """
    val = get_config(key, 'false')
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', '1', 'yes')


def _read_stamp():
    """Return the published cross-process stamp, or None if unavailable.

    Wrapped in a try/except so a missing cache backend or table during
    boot/tests does not crash callers — when the stamp can't be read we
    treat it as "no change" and let the in-process cache stand.
    """
    try:
        from django.core.cache import caches  # noqa: PLC0415
        return caches[_STAMP_CACHE_ALIAS].get(_STAMP_CACHE_KEY)
    except _CACHE_STAMP_EXCEPTIONS:
        logger.debug(
            'Unable to read integration settings cache stamp',
            exc_info=True,
        )
        return None


def _populate_cache():
    """Load all IntegrationSetting values into the in-process cache.

    Captures the published stamp BEFORE reading rows, so if another
    process writes a new stamp + new row between our reads we'll see a
    fresh stamp on the next call and repopulate again.
    """
    global _cache, _cache_populated, _cache_stamp
    try:
        from integrations.models import IntegrationSetting  # noqa: PLC0415
        stamp_at_read = _read_stamp()
        _cache = dict(IntegrationSetting.objects.values_list('key', 'value'))
        _cache_populated = True
        _cache_stamp = stamp_at_read
    except _DB_CONFIG_EXCEPTIONS:
        # Failed populate (DB unreachable, schema not migrated yet,
        # etc.) — do NOT mutate the stamp so the next call retries
        # rather than locking in a stale stamp.
        _cache_populated = False
        logger.warning(
            'Unable to populate integration config cache',
            exc_info=True,
        )


def clear_config_cache():
    """Clear the in-process cache and publish a fresh cross-process stamp.

    Called by ``studio.views.settings.settings_save_group`` after a
    successful upsert/delete on ``IntegrationSetting``. Other processes
    notice the new stamp on their next ``get_config()`` and repopulate.
    """
    global _cache, _cache_populated, _cache_stamp
    _cache = {}
    _cache_populated = False
    _cache_stamp = None
    try:
        from django.core.cache import caches  # noqa: PLC0415
        caches[_STAMP_CACHE_ALIAS].set(_STAMP_CACHE_KEY, uuid.uuid4().hex)
    except _CACHE_STAMP_EXCEPTIONS:
        # If the shared cache is unreachable we still cleared in-process
        # state, so the calling process at least sees fresh values.
        logger.warning(
            'Unable to publish integration settings cache stamp',
            exc_info=True,
        )
