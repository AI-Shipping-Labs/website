"""Compatibility serialization for the retired Studio transfer endpoints.

Two pure functions wrapping package settings and ``SocialApp``:

- ``build_export()`` — snapshot package-owned settings and auth providers.
- ``apply_import(payload)`` — upsert each entry from a previously-exported
  document. Unknown keys / providers are skipped (not rejected) so an export
  from a slightly-different schema version still bootstraps the bulk of a
  fresh environment.

The active settings page uses ``community_base.config.service`` directly.
These helpers remain for callers migrating from the old format; they never
read the legacy ``IntegrationSetting`` table and package service redaction is
preserved for secrets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from allauth.socialaccount.models import SocialApp
from community_base.kernel.redaction import REDACTED
from django.contrib.sites.models import Site

from integrations.config import set_package_override
from integrations.settings_registry import INTEGRATION_GROUPS
from studio.services.auth_settings import (
    PROVIDER_META,
    SUPPORTED_PROVIDERS,
)

FORMAT_VERSION = 1


def _known_integration_keys() -> dict[str, dict]:
    """Map of registered integration ``key`` -> key definition dict.

    Used by both export (look up ``group`` / ``is_secret`` for new rows on
    import) and import (whitelist the keys we know about).
    """
    out: dict[str, dict] = {}
    for group in INTEGRATION_GROUPS:
        for key_def in group['keys']:
            out[key_def['key']] = {**key_def, 'group': group['name']}
    return out


def build_export() -> dict:
    """Return the JSON-serialisable settings snapshot.

    Includes every package-registered setting and every ``SocialApp`` row
    whose ``provider`` is in ``SUPPORTED_PROVIDERS``. Package redaction is
    preserved for secret values.
    """
    from community_base.config.service import export as package_export

    # Package export is the sole source for runtime settings. It returns the
    # package's ``REDACTED`` marker for secret definitions, so credentials can
    # never be copied into a downloadable payload.
    integration_rows = sorted(package_export().items())
    integration_settings = [
        {'key': key, 'value': value} for key, value in integration_rows
    ]

    auth_providers = []
    for provider in SUPPORTED_PROVIDERS:
        app = SocialApp.objects.filter(provider=provider).first()
        if app is None:
            continue
        auth_providers.append({
            'provider': provider,
            'name': app.name or PROVIDER_META[provider]['name'],
            'client_id': app.client_id or '',
            # OAuth credentials are outside community-base's settings table,
            # but this retired compatibility serializer must still obey the
            # same no-plaintext-export contract.
            'secret': REDACTED,
        })

    exported_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    return {
        'format_version': FORMAT_VERSION,
        'exported_at': exported_at,
        'integration_settings': integration_settings,
        'auth_providers': auth_providers,
    }


@dataclass
class ImportResult:
    """Outcome of applying an import payload.

    ``skipped_integration_keys`` and ``skipped_auth_providers`` carry the
    names of entries that were dropped because they aren't in our registry
    — surfaced as a Django messages warning so the operator knows what
    didn't make it across.
    """

    integration_created: int = 0
    integration_updated: int = 0
    auth_created: int = 0
    auth_updated: int = 0
    restart_required: bool = False
    skipped_integration_keys: list[str] = field(default_factory=list)
    skipped_auth_providers: list[str] = field(default_factory=list)


class ImportError(Exception):
    """Raised when the uploaded payload is malformed or unsupported."""


def apply_import(payload: dict) -> ImportResult:
    """Upsert settings from ``payload`` into the database.

    Validates ``format_version`` and the top-level shape, then walks the two
    arrays. Unknown integration keys and unknown auth providers are skipped
    and reported in the result rather than rejected — schema drift between
    environments is the whole point of having an import.

    Raises:
        ImportError: payload is not a dict, ``format_version`` is missing
            or unsupported, or the two arrays are the wrong type.
    """
    if not isinstance(payload, dict):
        raise ImportError('Settings file must be a JSON object.')

    version = payload.get('format_version')
    if version != FORMAT_VERSION:
        raise ImportError(
            f'Unsupported format_version: {version!r}. '
            f'This build only accepts format_version={FORMAT_VERSION}.'
        )

    integration_entries = payload.get('integration_settings', [])
    auth_entries = payload.get('auth_providers', [])

    if not isinstance(integration_entries, list):
        raise ImportError('"integration_settings" must be a list.')
    if not isinstance(auth_entries, list):
        raise ImportError('"auth_providers" must be a list.')

    result = ImportResult()
    known_keys = _known_integration_keys()

    for entry in integration_entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get('key')
        value = entry.get('value', '')
        if not key:
            continue
        if key not in known_keys:
            result.skipped_integration_keys.append(key)
            continue
        meta = known_keys[key]
        if meta.get('requires_restart', False):
            result.restart_required = True
        normalized_value = value if value is not None else ''
        if normalized_value == REDACTED:
            # Redacted secrets are placeholders in an export, not a request
            # to overwrite or clear the destination credential.
            continue
        # Empty entries are a valid way to clear an optional setting in an
        # export. Package integer coercion intentionally rejects ``None`` and
        # empty strings, so leave no override in that case.
        if normalized_value == '':
            from integrations.config import delete_package_override

            created = False
            updated = delete_package_override(key)
        else:
            from community_base.config.models import Setting

            existed = Setting.objects.filter(key=key).exists()
            set_package_override(
                key,
                normalized_value,
                actor_ref='settings-import',
                reason='Imported Studio settings',
            )
            created = not existed
            updated = existed
        if created:
            result.integration_created += 1
        elif updated:
            result.integration_updated += 1

    site = Site.objects.get_current()
    for entry in auth_entries:
        if not isinstance(entry, dict):
            continue
        provider = entry.get('provider')
        if not provider:
            continue
        if provider not in SUPPORTED_PROVIDERS:
            result.skipped_auth_providers.append(provider)
            continue
        meta = PROVIDER_META[provider]
        name = entry.get('name') or meta['name']
        secret = entry.get('secret', '') or ''
        if secret == REDACTED:
            # A redacted export must preserve an existing credential. A new
            # provider gets an empty secret and can be completed in Studio.
            existing = SocialApp.objects.filter(provider=provider).first()
            secret = existing.secret if existing else ''
        app, created = SocialApp.objects.update_or_create(
            provider=provider,
            defaults={
                'name': name,
                'client_id': entry.get('client_id', '') or '',
                'secret': secret,
            },
        )
        app.sites.add(site)
        if created:
            result.auth_created += 1
        else:
            result.auth_updated += 1

    return result
