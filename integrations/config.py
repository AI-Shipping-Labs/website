"""Configuration helper for integration settings (community_base shim).

Issue #1584 moved integration settings storage into the
``community_base.config`` package (package tag v0.3.5). This module keeps
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
donor stamp channel stays watched (shared ``caches['django_q']``
DatabaseCache under ``integration_settings_stamp``, read through the
donor's ``integrations.shared_cache`` memo, local TTL <= 5s), and the
package service's own stamp joins it after the step-5 cutover, so a save
through either channel becomes visible here no later than the TTL bound. Each process records the stamp it saw when it last read
the DB; ``get_config()`` re-reads the stamp on every call and, if it
changed, repopulates the in-process cache from the DB. A warm request
therefore pays at most one stamp GET, not one GET per ``get_config()``
call, and zero ``cb_config`` queries when nothing has changed.

Transitional dual read: since the step-5 cutover every writer (Studio
groups, import, the operator API) goes through the package service, so the
database layer reads the package store FIRST and keeps the donor
``IntegrationSetting`` store as a read-only fallback for rows nothing has
rewritten yet. A package row therefore always wins over its stale donor
twin, and donor-only keys still resolve. Step 6 removes the donor leg
with the table itself. Package-decoded values are re-stringified to the
donor's raw-string shape (``true``/``false``, decimal integers) so every
consumer contract survives the cutover unchanged.

The stamp is intentionally opaque (a random uuid hex). We never compare
the value, only "is it the same string we recorded last time".
"""

import json
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

from integrations.shared_cache import get_shared_cache, set_shared_cache

# The donor's stamp channel, preserved verbatim (contract step 4): the
# shared ``django_q`` DatabaseCache, which every host that talks to the
# application DB already has (see ``website.settings`` CACHES).
_STAMP_CACHE_KEY = "integration_settings_stamp"

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
    # The package service stores coerced values (JSON booleans become real
    # bools, integers ints); the donor contract is raw lowercase strings.
    value = _donor_shaped(value)
    return value or None


def _donor_db_value(key):
    """Return the raw donor ``IntegrationSetting`` value for ``key``, or None.

    The donor stored plaintext and read any row regardless of declarations,
    so no registry lookup gates this read.
    """
    from integrations.models import IntegrationSetting  # noqa: PLC0415

    # This is a read-only migration fallback. Secret rows may still be
    # plaintext until migration 0030 runs, so they must never enter runtime
    # resolution or the compatibility cache.
    return (
        IntegrationSetting.objects.filter(key=key, is_secret=False)
        .values_list("value", flat=True)
        .first()
    )


def _donor_shaped(value):
    """Re-stringify a decoded package value into the donor's raw shape.

    The donor contract is raw strings: booleans arrive as ``'true'`` /
    ``'false'`` and integers as decimal strings. The package service stores
    the coerced Python values, so a package-written row is converted back
    before it reaches any consumer.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        # A list-type key: the donor stored the comma-joined raw string the
        # Studio form submitted.
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return value


def _package_db_value(key):
    """Return the decrypted, donor-shaped package value for ``key``, or None."""
    from community_base.config.models import Setting  # noqa: PLC0415

    try:
        item = definition(key)
    except (ImproperlyConfigured, KeyError):
        # The package registry raises KeyError for an undeclared key; the
        # donor contract is that such keys resolve through settings/env.
        return None
    stored = Setting.objects.filter(key=key).values_list("value", flat=True).first()
    if stored is None:
        return None
    return _donor_shaped(_decode_stored_value(key, item, stored))


def _donor_fallback_suppressed(key):
    """Return whether package state intentionally masks the donor row.

    A deleted package row is otherwise indistinguishable from a key that has
    never migrated.  The package change log records an explicit clear with a
    null ``new_value``; that tombstone keeps an old donor value from being
    resurrected while retaining the donor as a read-only migration fallback.
    """
    try:
        from community_base.config.models import Setting, SettingChange  # noqa: PLC0415

        if Setting.objects.filter(key=key).exists():
            return True
        change = (
            SettingChange.objects.filter(setting_key=key)
            .order_by("-created_at", "-pk")
            .first()
        )
        return change is not None and change.new_value is None
    except _DB_CONFIG_EXCEPTIONS:
        # During a rolling deploy the audit table may not be available yet;
        # retain the donor read fallback until the package schema is ready.
        return False


def _read_db_value(key):
    """Return the stored value for ``key`` as a raw string, or None.

    Package store first — every writer has gone through the package
    service since the step-5 cutover, so a package row is authoritative
    over its stale donor twin — then the donor store as a read-only
    fallback for rows nothing has rewritten. Keys the package registry
    does not declare have no package storage layer (``definition()``
    raises ImproperlyConfigured for them) and resolve through the donor
    store, settings, and env only. Database errors propagate to the
    caller, which handles them with the donor's log-and-fall-through set.
    """
    package_value = _package_db_value(key)
    if package_value is not None:
        return package_value

    donor_value = _donor_db_value(key)
    if donor_value is not None and not _donor_fallback_suppressed(key):
        return donor_value
    return None


def _db_configured(key):
    """Probe whether ``key`` has a non-empty stored cb_config value.

    Mirrors the "non-empty value counts as configured" rule used by
    ``_get_config_uncached`` (which short-circuits on truthy
    ``db_value``) and by ``settings_save_group`` (which deletes the row
    on empty string). The decrypted plaintext never leaves this helper —
    :func:`resolve_source` only ever returns the layer name.
    """
    package_value = _package_db_value(key)
    if package_value is not None:
        return bool(package_value)

    donor_value = _donor_db_value(key)
    if donor_value is not None and not _donor_fallback_suppressed(key):
        return bool(donor_value)
    return False


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


def recording_transcript_ingest_enabled():
    """True when the automatic transcript pipeline should run (issue #1597).

    Gates the automatic enqueue points (the Zoom webhooks and the post-S3-
    upload chain) but never the explicit operator recovery path
    (``POST /api/events/<slug>/sync-transcript`` and the Studio sync action),
    so an operator can always backfill a transcript with automation off.
    Default-on: a finished call should end with a stored transcript without
    anyone pressing anything. Reads via
    ``get_config('RECORDING_TRANSCRIPT_INGEST_ENABLED', 'true')`` so the
    DB -> settings -> env -> default chain resolves to True when the key is
    unset everywhere, mirroring
    :func:`recording_auto_publish_on_s3_upload_enabled`.
    """
    raw = get_config('RECORDING_TRANSCRIPT_INGEST_ENABLED', 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')


def recording_recap_auto_draft_enabled():
    """True when a stored transcript should chain an LLM recap draft.

    Issue #1597. Default-on per the product decision recorded on the issue:
    the drafted recap is saved to ``recap_notes`` (never overwriting
    operator-authored notes), and the public recap page then goes live
    through the existing ``recap_is_published`` gate — publication is
    deliberate, notification of registrants stays explicit. Reads via
    ``get_config('RECORDING_RECAP_AUTO_DRAFT_ENABLED', 'true')`` following
    the same default-on pattern as
    :func:`recording_auto_publish_on_s3_upload_enabled`.
    """
    raw = get_config('RECORDING_RECAP_AUTO_DRAFT_ENABLED', 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')


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
    """Return both published stamps, or None when neither is readable.

    Since the step-5 cutover the package service publishes into the
    default cache under its ``STAMP_KEY`` on every Studio/API save; the
    donor channel (``django_q`` under ``integration_settings_stamp``) is
    still watched so any surviving pre-cutover writer invalidates too.
    Each tuple entry is None when its channel never published; the outer
    None means both channels were unreadable, which the caller treats as
    "no change" and lets the in-process cache stand.
    """
    package_stamp = None
    donor_stamp = None
    try:
        from community_base.config.service import STAMP_KEY  # noqa: PLC0415
        from django.core.cache import cache  # noqa: PLC0415

        package_stamp = cache.get(STAMP_KEY)
    except _CACHE_STAMP_EXCEPTIONS:
        logger.debug(
            "Unable to read the package config stamp",
            exc_info=True,
        )
    try:
        donor_stamp = get_shared_cache(_STAMP_CACHE_KEY)
    except _CACHE_STAMP_EXCEPTIONS:
        logger.debug(
            "Unable to read integration settings cache stamp",
            exc_info=True,
        )
    if package_stamp is None and donor_stamp is None:
        return None
    return (package_stamp, donor_stamp)


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
            except (ImproperlyConfigured, KeyError):
                continue
            value = _decode_stored_value(row_key, item, stored)
            if value:
                values[row_key] = value
        # Donor rows fill only the gaps: the package rows are authoritative
        # since every writer moved to the package service (see the module
        # docstring's transitional dual read).
        from integrations.models import IntegrationSetting  # noqa: PLC0415

        donor_rows = list(
            IntegrationSetting.objects.filter(is_secret=False).values_list("key", "value")
        )
        donor_keys = {row_key for row_key, _ in donor_rows}
        clear_keys = set()
        if donor_keys:
            from community_base.config.models import SettingChange  # noqa: PLC0415

            seen = set()
            changes = SettingChange.objects.filter(
                setting_key__in=donor_keys,
            ).order_by("-created_at", "-pk").values_list("setting_key", "new_value")
            for row_key, new_value in changes:
                if row_key in seen:
                    continue
                seen.add(row_key)
                if new_value is None:
                    clear_keys.add(row_key)
        package_keys = set(
            Setting.objects.values_list("key", flat=True)
        )
        for row_key, value in donor_rows:
            if value and row_key not in values and row_key not in package_keys and row_key not in clear_keys:
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


def set_package_override(key, value, actor_ref, reason=""):
    """Persist an override in package storage.

    The legacy ``IntegrationSetting`` table is intentionally read-only during
    the one-release transition. In particular, mirroring a package secret
    there would put plaintext credentials back into the database. Existing
    donor rows remain available only for non-secret read fallback until the
    second migration removes that table.
    """
    from community_base.config.service import set as package_set  # noqa: PLC0415

    return package_set(key, value, actor_ref=actor_ref, reason=reason)


def delete_package_override(key) -> bool:
    """Delete a package override without mutating the donor table.

    This helper is retained for import callers using the former format. The
    donor table is a read-only migration source, so clearing a package value
    never writes to it. If no package row exists, the package service returns
    ``False`` and the unmigrated donor fallback remains unchanged.
    """
    from community_base.config.service import unset as package_unset  # noqa: PLC0415

    package_deleted = package_unset(
        key,
        actor_ref="settings-import",
        reason="Cleared package settings override",
    )
    reset_local_config_cache()
    return bool(package_deleted)


def clear_config_cache():
    """Clear the in-process cache and publish a fresh cross-process stamp.

    Called after a successful package settings save/delete. The donor channel
    is kept: a fresh stamp is published into the shared ``django_q`` cache;
    other processes notice it on their next ``get_config()`` and
    repopulate.
    """
    reset_local_config_cache()
    try:
        import uuid  # noqa: PLC0415

        set_shared_cache(_STAMP_CACHE_KEY, uuid.uuid4().hex)
    except _CACHE_STAMP_EXCEPTIONS:
        # If the shared cache is unreachable we still cleared in-process
        # state, so the calling process at least sees fresh values.
        logger.warning(
            "Unable to publish integration settings cache stamp",
            exc_info=True,
        )
