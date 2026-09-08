"""Configuration helper for integration settings (community_base shim).

Issue #1584 moved integration settings storage into the
``community_base.config`` package (package tag v0.3.0). This module keeps
the historical ``integrations.config`` import path and the exact donor
resolution semantics, backed by package storage:

- ``get_config()`` checks the database first, then Django settings, then
  environment variables, then the caller's default. Web processes use an
  in-process cache that is cleared when settings are saved via Studio.
  Worker processes bypass that cache and read runtime config fresh.
- The database layer reads ``community_base.config.models.Setting`` rows
  (app label ``cb_config``) directly. Keys the package registry does not
  declare are legal: they simply resolve through settings/env/default.
  Values are returned as raw strings — the shim never applies the
  package ``Definition.coerce`` and never raises for data problems,
  matching the donor, which stored plain strings.
- Keys whose package definition has ``secret=True`` are stored as
  ``fernet:v1:`` ciphertext (``community_base.config.crypto``, keyed from
  ``settings.SECRET_KEY``). The shim decrypts on read; an empty string
  after decryption counts as unset and falls through, exactly like an
  empty donor row did.

Cross-process invalidation
==========================

The donor published a short stamp into ``caches['django_q']`` and memoized
stamp reads through ``integrations.shared_cache`` (local TTL <= 5s). That
machinery is replaced by the package runtime
(``community_base.config.service.runtime``): the stamp now lives in the
DEFAULT Django cache under the package's ``STAMP_KEY``, which must be
process-shared in deployment — the package makes the same assumption for
its own runtime. Each process records the stamp it saw when it last read
the DB; ``get_config()`` re-reads the stamp on every call and, if it
changed, repopulates the in-process cache from the DB. A warm request
therefore pays at most one stamp GET, not one GET per ``get_config()``
call, and zero ``cb_config`` queries when nothing has changed.

The stamp is intentionally opaque (a random uuid hex). We never compare
the value, only "is it the same string we recorded last time".
"""

import logging
import os
import sys

from community_base.config.crypto import decrypt
from community_base.config.registry import definition
from django.apps.registry import AppRegistryNotReady
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError
from django.test.testcases import DatabaseOperationForbidden

# Must match community_base.config.service.STAMP_KEY (pinned package tag
# v0.3.0); pinned by integrations.tests.test_config_shim. The constant
# lives here (instead of importing service at module level) because
# community_base.config.service imports the cb_config models, which only
# exist once the app registry is ready.
_STAMP_CACHE_KEY = "community_base.config.stamp"

logger = logging.getLogger(__name__)

_DB_CONFIG_EXCEPTIONS = (AppRegistryNotReady, ImproperlyConfigured, DatabaseError)
_DB_TEST_ISOLATION_EXCEPTIONS = (DatabaseOperationForbidden,)
_CACHE_STAMP_EXCEPTIONS = (
    InvalidCacheBackendError,
    ImproperlyConfigured,
    DatabaseError,
)
# Data problems while decoding a stored row: crypto.decrypt raises
# ImproperlyConfigured for a missing/broken 'fernet:v1:' prefix or an
# undecryptable token, and json.loads inside it raises a ValueError
# subclass for a payload that is not JSON. Both mean "bad row", never
# "crash the caller".
_DECODE_EXCEPTIONS = (ImproperlyConfigured, ValueError)

_cache = {}
_cache_populated = False
_cache_stamp = None


def get_config(key, default="", *, use_settings=True):
    """Get config value: DB first, then Django settings, then env var, then default.

    Args:
        key: The setting key (e.g. 'ZOOM_CLIENT_ID').
        default: Default value if not found anywhere.
        use_settings: When False, skip the Django settings layer.

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


def validate_email_config_value(key, value):
    """Return a stripped email setting, or ``''`` when it is malformed.

    Log only the setting key, never its potentially sensitive or
    attacker-controlled value.
    """
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        validate_email(value)
    except ValidationError:
        logger.error(
            "Invalid email address configured for %s; omitting it from email delivery",
            key,
        )
        return ""
    return value


def running_in_worker_process():
    """Return True when executing inside a Django-Q worker process."""
    if os.environ.get("DJANGO_QCLUSTER_PROCESS") == "true":
        return True
    return any(arg == "qcluster" for arg in sys.argv)


def _decode_stored_value(key, item, stored):
    """Decode one stored cb_config value into the raw string the donor returned.

    Secret definitions store ``fernet:v1:`` ciphertext; everything else is
    the stored JSON scalar. The package ``value_type`` is intentionally NOT
    applied — the donor never coerced and never raised, so this returns the
    stored value as a plain string. Returns None when the value is empty,
    undecryptable or otherwise unusable; the caller then falls through to
    settings/env/default.
    """
    value = stored
    if item.secret:
        try:
            value = decrypt(stored)
        except _DECODE_EXCEPTIONS:
            logger.warning(
                "Unable to decrypt integration config value from database",
                exc_info=True,
                extra={"config_key": key},
            )
            return None
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return value or None


def _read_db_value(key):
    """Return the stored cb_config value for ``key`` as a raw string, or None.

    Keys the package registry does not declare have no package storage
    layer (``definition()`` raises ImproperlyConfigured for them) and
    resolve through settings/env only. Database errors propagate to the
    caller, which handles them with the donor's log-and-fall-through set.
    """
    from community_base.config.models import Setting  # noqa: PLC0415

    try:
        item = definition(key)
    except ImproperlyConfigured:
        return None
    stored = Setting.objects.filter(key=key).values_list("value", flat=True).first()
    if stored is None:
        return None
    return _decode_stored_value(key, item, stored)


def _db_configured(key):
    """Probe whether ``key`` has a non-empty stored cb_config value.

    Mirrors the "non-empty value counts as configured" rule used by
    ``_get_config_uncached`` (which short-circuits on truthy
    ``db_value``) and by ``settings_save_group`` (which deletes the row
    on empty string). The decrypted plaintext never leaves this helper —
    :func:`resolve_source` only ever returns the layer name.
    """
    from community_base.config.models import Setting  # noqa: PLC0415

    try:
        item = definition(key)
    except ImproperlyConfigured:
        return False
    stored = Setting.objects.filter(key=key).values_list("value", flat=True).first()
    if stored is None:
        return False
    return bool(_decode_stored_value(key, item, stored))


def _get_config_uncached(key, default="", *, use_settings=True):
    """Read one config key without touching the in-process DB cache."""
    try:
        db_value = _read_db_value(key)
        if db_value:
            return db_value
    except _DB_TEST_ISOLATION_EXCEPTIONS:
        pass
    except _DB_CONFIG_EXCEPTIONS:
        logger.warning(
            "Unable to read integration config from database",
            exc_info=True,
            extra={"config_key": key},
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


def resolve_source(key, *, registry_default=""):
    """Return where the value for ``key`` is currently coming from.

    Mirrors the lookup order in :func:`_get_config_uncached`:
    cb_config Setting row with a non-empty (decrypted) value, then
    ``django.conf.settings``, then the process environment, then the
    registry default. Returns ``None`` when the key is unset everywhere.

    Intentionally split into separate probes — the DB probe, ``hasattr``,
    ``in os.environ`` — so the value itself is never returned to the
    caller. The integration settings API uses this on the read path to
    expose ``configured`` / ``source`` without ever exposing the value
    (issue #640).
    """
    try:
        if _db_configured(key):
            return "db"
    except _DB_TEST_ISOLATION_EXCEPTIONS:
        pass
    except _DB_CONFIG_EXCEPTIONS:
        logger.warning(
            "Unable to probe integration config source from database",
            exc_info=True,
            extra={"config_key": key},
        )

    from django.conf import settings  # noqa: PLC0415

    if settings.configured and hasattr(settings, key) and getattr(settings, key, None) is not None:
        return "django_settings"

    if key in os.environ:
        return "env"

    if registry_default:
        return "default"

    return None


def site_base_url():
    """Resolved canonical site URL: DB override > env value > default."""
    from django.conf import settings  # noqa: PLC0415

    return get_config("SITE_BASE_URL", settings.SITE_BASE_URL)


def s3_content_upload_enabled():
    """True when content-image S3 uploads are enabled (issue #1131).

    Default-on: a missing or drifted ``S3_ENABLED`` env var (as happened in
    the prod worker container) must NOT silently disable uploads. Reads via
    ``get_config('S3_ENABLED', 'true')`` so the DB -> settings -> env -> default
    chain resolves to ``True`` when the key is unset everywhere, mirroring the
    canonical default-on pattern in ``questionnaires/onboarding.py``.

    Unlike :func:`is_enabled`, which hardcodes a ``'false'`` fallback, this
    helper defaults the flag on so only an explicit falsey value (DB override,
    Django setting, or env var) disables uploads.
    """
    raw = get_config("S3_ENABLED", "true")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes")


def recording_auto_publish_on_s3_upload_enabled():
    """True when a successful S3 recording upload should auto-publish the event.

    Issue #1134 (Phase B). Default-on per the product decision: the recording
    should be watchable right away, so entitled members can watch as soon as
    the upload finishes. Reads via
    ``get_config('RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD', 'true')`` so the
    DB -> settings -> env -> default chain resolves to True when the key is
    unset everywhere, mirroring :func:`s3_content_upload_enabled`.

    Unlike :func:`is_enabled`, which hardcodes a ``'false'`` fallback, this
    helper defaults the flag on so only an explicit falsey value (a DB override
    set from Studio, a Django setting, or an env var) restores the review-first
    flow.
    """
    raw = get_config("RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD", "true")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes")


def is_enabled(key):
    """Check if a config flag is enabled (handles both bool and string values).

    Args:
        key: The setting key (e.g. 'SLACK_ENABLED').

    Returns:
        bool: True if the value is truthy ('true', True, '1').
    """
    val = get_config(key, "false")
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def _read_stamp():
    """Return the published cross-process stamp, or None if unavailable.

    The stamp lives in the DEFAULT Django cache under the package's key
    (``community_base.config.service.STAMP_KEY``). Wrapped in a try/except
    so a missing cache backend or table during boot/tests does not crash
    callers — when the stamp can't be read we treat it as "no change" and
    let the in-process cache stand.
    """
    try:
        from django.core.cache import cache  # noqa: PLC0415

        return cache.get(_STAMP_CACHE_KEY)
    except _CACHE_STAMP_EXCEPTIONS:
        logger.debug(
            "Unable to read integration settings cache stamp",
            exc_info=True,
        )
        return None


def _populate_cache():
    """Load all declared cb_config Setting values into the in-process cache.

    Every ``cb_config`` Setting row is read (the donor likewise read all
    rows); rows for keys the package registry does not declare are
    ignored. Captures the published stamp BEFORE reading rows, so if
    another process writes a new stamp + new row between our reads we'll
    see a fresh stamp on the next call and repopulate again.
    """
    global _cache, _cache_populated, _cache_stamp
    try:
        from community_base.config.models import Setting  # noqa: PLC0415

        stamp_at_read = _read_stamp()
        values = {}
        for row_key, stored in Setting.objects.values_list("key", "value"):
            try:
                item = definition(row_key)
            except ImproperlyConfigured:
                continue
            value = _decode_stored_value(row_key, item, stored)
            if value:
                values[row_key] = value
        _cache = values
        _cache_populated = True
        _cache_stamp = stamp_at_read
    except _DB_TEST_ISOLATION_EXCEPTIONS:
        _cache_populated = False
    except _DB_CONFIG_EXCEPTIONS:
        # Failed populate (DB unreachable, schema not migrated yet,
        # etc.) — do NOT mutate the stamp so the next call retries
        # rather than locking in a stale stamp.
        _cache_populated = False
        logger.warning(
            "Unable to populate integration config cache",
            exc_info=True,
        )


def reset_local_config_cache():
    """Drop THIS process's cache without publishing anything.

    ``clear_config_cache()`` also publishes a cross-process stamp via the
    package runtime. That is correct for a Studio save but wrong for a test
    harness that has to reset state around every test, including tests with
    no database access at all.

    This is the pure in-memory half: it resets the shim cache and the
    package runtime's memo; the next ``get_config()`` repopulates from
    whatever the database currently holds (or, when there is no usable
    database, falls through to settings/env exactly as on a cold start).
    """
    global _cache, _cache_populated, _cache_stamp
    _cache = {}
    _cache_populated = False
    _cache_stamp = None
    from community_base.config.service import runtime  # noqa: PLC0415

    runtime.reset()


def clear_config_cache():
    """Clear the in-process cache and publish a fresh cross-process stamp.

    Called by ``studio.views.settings.settings_save_group`` after a
    successful upsert/delete on integration settings. The package runtime
    publishes the stamp into the default cache; other processes notice the
    new stamp on their next ``get_config()`` and repopulate.
    """
    reset_local_config_cache()
    try:
        from community_base.config.service import runtime  # noqa: PLC0415

        runtime.publish()
    except _CACHE_STAMP_EXCEPTIONS:
        # If the shared cache is unreachable we still cleared in-process
        # state, so the calling process at least sees fresh values.
        logger.warning(
            "Unable to publish integration settings cache stamp",
            exc_info=True,
        )
