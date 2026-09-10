"""Build and apply Studio settings export/import payloads (issue #323).

Two pure functions wrapped around the package config store (``cb_config``,
through :mod:`integrations.config`'s cutover helpers) and ``SocialApp``:

- ``build_export()`` — snapshot every known integration key + auth provider
  row in plaintext, returned as a JSON-serialisable dict with
  ``format_version: 1``.
- ``apply_import(payload)`` — upsert each entry from a previously-exported
  document. Unknown keys / providers are skipped (not rejected) so an export
  from a slightly-different schema version still bootstraps the bulk of a
  fresh environment.

The endpoints in ``studio/views/settings.py`` are thin shells around these
functions so the logic is straightforward to unit-test without a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError

from integrations.config import (
    delete_package_override,
    has_package_override,
    set_package_override,
    stored_override_map,
)
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
        for key_def in group["keys"]:
            out[key_def["key"]] = {**key_def, "group": group["name"]}
    return out


def build_export() -> dict:
    """Return the JSON-serialisable settings snapshot.

    Includes every stored override whose key is registered in
    ``INTEGRATION_GROUPS`` and every ``SocialApp`` row whose ``provider`` is
    in ``SUPPORTED_PROVIDERS``. Values are plaintext.
    """
    known_keys = _known_integration_keys()

    overrides = stored_override_map()
    integration_settings = [
        {"key": key, "value": overrides[key]} for key in sorted(known_keys.keys() & overrides.keys())
    ]

    auth_providers = []
    for provider in SUPPORTED_PROVIDERS:
        app = SocialApp.objects.filter(provider=provider).first()
        if app is None:
            continue
        auth_providers.append(
            {
                "provider": provider,
                "name": app.name or PROVIDER_META[provider]["name"],
                "client_id": app.client_id or "",
                "secret": app.secret or "",
            }
        )

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "format_version": FORMAT_VERSION,
        "exported_at": exported_at,
        "integration_settings": integration_settings,
        "auth_providers": auth_providers,
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
    invalid_integration_keys: list[str] = field(default_factory=list)
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
        raise ImportError("Settings file must be a JSON object.")

    version = payload.get("format_version")
    if version != FORMAT_VERSION:
        raise ImportError(
            f"Unsupported format_version: {version!r}. This build only accepts format_version={FORMAT_VERSION}."
        )

    integration_entries = payload.get("integration_settings", [])
    auth_entries = payload.get("auth_providers", [])

    if not isinstance(integration_entries, list):
        raise ImportError('"integration_settings" must be a list.')
    if not isinstance(auth_entries, list):
        raise ImportError('"auth_providers" must be a list.')

    result = ImportResult()
    known_keys = _known_integration_keys()

    for entry in integration_entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        value = entry.get("value", "")
        if not key:
            continue
        if key not in known_keys:
            result.skipped_integration_keys.append(key)
            continue
        meta = known_keys[key]
        if meta.get("requires_restart", False):
            result.restart_required = True
        # D1.2c step-5 cutover: overrides persist in the package config
        # store through the package service (encrypted secrets, audit row).
        # The donor import stored whatever it was given, including empty and
        # malformed values; the package coerces, so the donor's empty-means-
        # unset shape becomes a row deletion here and a value the package
        # refuses is skipped and reported instead of stored.
        stored_value = value if value is not None else ""
        if stored_value == "":
            if delete_package_override(key):
                result.integration_updated += 1
            continue
        created = not has_package_override(key)
        try:
            set_package_override(
                key,
                stored_value,
                actor_ref="studio:settings-import",
                reason="Imported Studio settings bundle",
            )
        except ValidationError:
            result.invalid_integration_keys.append(key)
            continue
        if created:
            result.integration_created += 1
        else:
            result.integration_updated += 1

    site = Site.objects.get_current()
    for entry in auth_entries:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider")
        if not provider:
            continue
        if provider not in SUPPORTED_PROVIDERS:
            result.skipped_auth_providers.append(provider)
            continue
        meta = PROVIDER_META[provider]
        name = entry.get("name") or meta["name"]
        app, created = SocialApp.objects.update_or_create(
            provider=provider,
            defaults={
                "name": name,
                "client_id": entry.get("client_id", "") or "",
                "secret": entry.get("secret", "") or "",
            },
        )
        app.sites.add(site)
        if created:
            result.auth_created += 1
        else:
            result.auth_updated += 1

    return result
