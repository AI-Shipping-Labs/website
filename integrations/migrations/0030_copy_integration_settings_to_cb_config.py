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
  declared there; otherwise the donor ``SETTING_VALUE_TYPES`` map is translated
  (``boolean`` -> ``bool``, ``integer`` -> ``int``, anything else -> ``str``).
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

from integrations.settings_registry import SETTING_VALUE_TYPES

BATCH_SIZE = 500

_DONOR_VALUE_TYPE_MAP = {
    'boolean': 'bool',
    'integer': 'int',
}


def _traits_for(key, donor_is_secret):
    """Return (value_type, secret) for a donor key.

    The package registry wins when the key is declared there; undeclared
    (ad-hoc) rows fall back to the donor type map and the donor row's own
    ``is_secret`` flag so a secret can never land in plaintext by accident.
    """
    try:
        declared = package_definition(key)
    except ImproperlyConfigured:
        return (
            _DONOR_VALUE_TYPE_MAP.get(SETTING_VALUE_TYPES.get(key), 'str'),
            donor_is_secret,
        )
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
