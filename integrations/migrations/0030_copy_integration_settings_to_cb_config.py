"""A0.2 step 3: copy IntegrationSetting rows into the package's cb_config.Setting.

One-off data migration for plan issue #1584. Every donor row is upserted by key
into the package storage, with these rules:

- Values whose package registry definition has ``secret=True`` are stored
  encrypted via ``community_base.config.crypto.encrypt`` (Fernet, ``fernet:v1:``
  prefix, key derived from ``settings.SECRET_KEY``). The stored value is a JSON
  string scalar; the package read path decrypts it and returns the raw string.
  Plaintext secrets are never logged.
- Non-secret values are stored as raw JSON string scalars.
- ``value_type`` comes from the package registry definition when the key is
  declared there; otherwise from the frozen donor snapshot below, translated
  (``boolean`` -> ``bool``, ``integer`` -> ``int``, anything else -> ``str``).
  The snapshot is inlined because migrations are replayed on every fresh
  install long after the live ``integrations.settings_registry`` module is
  deleted at the A0.2 cutover, and importing it here would break ``migrate``.
- ``source`` is ``db`` and ``updated_at`` is copied from the donor row.
- Re-running is idempotent: rows whose key already exists in ``cb_config`` are
  skipped, never overwritten.

The reverse is a deliberate no-op (copy only): the package rows stay valid after
a rollback and the donor ``IntegrationSetting`` table is dropped one release
later (playbook P6, second pull request).
"""

from community_base.config import crypto
from community_base.config.registry import definition as package_definition
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations

BATCH_SIZE = 1000

# Frozen snapshot of the donor ``integrations.settings_registry`` value types
# as of 2026-09-08 (17 booleans, 31 integers; every other donor key is a
# string, including the donor's ``url`` type). Only these two sets matter:
# undeclared keys outside them copy as ``str``.
_DONOR_BOOLEAN_KEYS = frozenset({
    'AUTHENTICATED_CHECKOUT_BINDING_ENABLED',
    'LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED',
    'LOGFIRE_ENABLED',
    'MAVEN_ENROLLMENT_ENABLED',
    'NEXT_SPRINT_DRAFT_USE_PROFILE',
    'ONBOARDING_AI_ENABLED',
    'ONBOARDING_AI_STREAMING',
    'ONBOARDING_REMINDER_ENABLED',
    'RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD',
    'S3_ENABLED',
    'SES_WEBHOOK_VALIDATION_ENABLED',
    'SLACK_ENABLED',
    'SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED',
    'STAFF_SLACK_JOIN_NOTIFY_ENABLED',
    'TRIGGERS_ENABLED',
    'ZOOM_JOIN_BEFORE_HOST',
    'ZOOM_WAITING_ROOM',
})
_DONOR_INTEGER_KEYS = frozenset({
    'AUTH_THROTTLE_LOGIN_EMAIL_LIMIT',
    'AUTH_THROTTLE_LOGIN_IP_LIMIT',
    'AUTH_THROTTLE_LOGIN_WINDOW_SECONDS',
    'AUTH_THROTTLE_MAIL_EMAIL_LIMIT',
    'AUTH_THROTTLE_MAIL_IP_LIMIT',
    'AUTH_THROTTLE_MAIL_WINDOW_SECONDS',
    'BANNER_GENERATOR_TIMEOUT_SECONDS',
    'BANNER_UPLOAD_MAX_MB',
    'CALENDLY_WEBHOOK_RETENTION_DAYS',
    'CALENDLY_WEBHOOK_TOLERANCE_SECONDS',
    'CAMPAIGN_BATCH_INTERVAL_SECONDS',
    'CAMPAIGN_DELIVERY_MAX_ATTEMPTS',
    'CHECKOUT_BINDING_TTL_MINUTES',
    'CRM_EXPORT_MAX_LIMIT',
    'DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS',
    'DOWNLOAD_PRESIGNED_URL_TTL_SECONDS',
    'EMAIL_BATCH_SIZE',
    'LLM_MAX_RETRIES',
    'MAVEN_OVERRIDE_DURATION_DAYS',
    'ONBOARDING_AI_DEADLINE_SECONDS',
    'ONBOARDING_AI_MAX_ATTEMPTS',
    'ONBOARDING_REMINDER_DELAY_DAYS',
    'PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS',
    'PLAN_SPRINTS_INGEST_LEASE_MINUTES',
    'PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS',
    'PLAN_SPRINTS_THREAD_REFRESH_DAYS',
    'RECORDING_PRESIGNED_URL_TTL_SECONDS',
    'SPRINT_BADGE_WINDOW_DAYS',
    'UNVERIFIED_USER_TTL_DAYS',
    'USER_ACTIVITY_RETENTION_DAYS',
    'ZOOM_WEBHOOK_TOLERANCE_SECONDS',
})


def _donor_value_type(key):
    if key in _DONOR_BOOLEAN_KEYS:
        return 'bool'
    if key in _DONOR_INTEGER_KEYS:
        return 'int'
    return 'str'


def _traits_for(key, donor_is_secret):
    """Return (value_type, secret) for a donor key.

    The package registry wins when the key is declared there; undeclared
    (ad-hoc) rows fall back to the frozen donor snapshot and the donor row's
    own ``is_secret`` flag so a secret can never land in plaintext by accident.
    """
    try:
        declared = package_definition(key)
    except ImproperlyConfigured:
        return _donor_value_type(key), donor_is_secret
    return declared.value_type, declared.secret


def _copy_batch(Setting, rows):
    existing_keys = set(
        Setting.objects.filter(key__in=[row.key for row in rows]).values_list('key', flat=True)
    )
    for row in rows:
        if row.key in existing_keys:
            # Already copied by a previous run: leave the package row untouched
            # so re-runs never duplicate or clobber newer package-side edits.
            continue
        value_type, secret = _traits_for(row.key, row.is_secret)
        stored_value = crypto.encrypt(row.value) if secret else row.value
        setting = Setting.objects.create(
            key=row.key,
            value=stored_value,
            value_type=value_type,
            source='db',
        )
        # updated_at has auto_now=True, which QuerySet.update() bypasses, so the
        # donor timestamp is written with a follow-up update.
        Setting.objects.filter(pk=setting.pk).update(updated_at=row.updated_at)


def copy_integration_settings(apps, schema_editor):
    IntegrationSetting = apps.get_model('integrations', 'IntegrationSetting')
    Setting = apps.get_model('cb_config', 'Setting')

    batch = []
    for row in IntegrationSetting.objects.order_by('pk').iterator(chunk_size=BATCH_SIZE):
        batch.append(row)
        if len(batch) == BATCH_SIZE:
            _copy_batch(Setting, batch)
            batch = []
    if batch:
        _copy_batch(Setting, batch)


def uncopy_integration_settings(apps, schema_editor):
    # Intentional no-op: this migration only copies. The cb_config rows remain
    # authoritative after a rollback, and the donor IntegrationSetting table is
    # removed one release later (playbook P6, second pull request), so there is
    # nothing to restore here.
    pass


class Migration(migrations.Migration):

    # Atomic by default: the whole copy either lands or rolls back together.
    dependencies = [
        ('cb_config', '0001_initial'),
        ('integrations', '0029_webhooklog_calendly_event_uri_and_more'),
    ]

    operations = [
        migrations.RunPython(copy_integration_settings, uncopy_integration_settings),
    ]
