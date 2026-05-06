"""Build and apply Studio content-sources export/import payloads (issue #436).

Sibling of ``studio/services/settings_io.py`` — same shape, same conventions:

- ``build_export()`` snapshots every ``ContentSource`` row's operator-config
  fields (repo_name, webhook_secret, is_private, max_files) in plaintext and
  returns a JSON-serialisable dict with ``format_version: 1``.
- ``apply_import(payload)`` upserts content sources keyed on ``repo_name``.
  Runtime-state fields (last_synced_at, last_sync_status, sync_locked_at,
  etc.) are NEVER written to — those belong to the sync worker, not the
  operator-bootstrap flow.

Webhook secrets are exported in plaintext on purpose. Without them the file
does not save the operator any work; without the secret the operator would
have to re-paste it from a password manager into every repo on a fresh
environment. The view layer surfaces a sensitivity disclaimer to the
operator both in-page and in the success flash.

The endpoints in ``studio/views/sync.py`` are thin shells around these
functions so the logic is straightforward to unit-test without a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from integrations.models import ContentSource

FORMAT_VERSION = 1


def build_export() -> dict:
    """Return the JSON-serialisable content-sources snapshot.

    Lists every ``ContentSource`` row ordered by ``repo_name`` so the file
    is deterministic and a ``git diff`` between two exports is meaningful.
    Only the four operator-config fields round-trip; runtime-state fields
    (last_synced_at, last_sync_status, sync_locked_at, ...) stay home.
    """
    rows = ContentSource.objects.order_by('repo_name').values(
        'repo_name', 'webhook_secret', 'is_private', 'max_files',
    )
    content_sources = [
        {
            'repo_name': row['repo_name'],
            'webhook_secret': row['webhook_secret'],
            'is_private': row['is_private'],
            'max_files': row['max_files'],
        }
        for row in rows
    ]

    exported_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    return {
        'format_version': FORMAT_VERSION,
        'exported_at': exported_at,
        'content_sources': content_sources,
    }


@dataclass
class ImportResult:
    """Outcome of applying a content-sources import payload.

    ``skipped_repos`` collects entries the importer rejected because their
    ``repo_name`` was missing, empty, or not a string. The view surfaces it
    as a Django messages warning so the operator knows what didn't make it.
    Entries are stored as ``<entry #N>`` placeholders rather than echoing
    untrusted payload text back.
    """

    created: int = 0
    updated: int = 0
    skipped_repos: list[str] = field(default_factory=list)


class ImportError(Exception):
    """Raised when the uploaded payload is malformed or unsupported."""


def _coerce_max_files(raw, default: int = 1000) -> int:
    """Coerce ``raw`` to a non-negative int, falling back to ``default``.

    Negative values are clamped to ``0`` (matches the
    ``PositiveIntegerField`` constraint on the model). Anything that fails
    ``int(...)`` falls back to the default so a malformed entry doesn't
    break the whole import.
    """
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(value, 0)


def apply_import(payload: dict) -> ImportResult:
    """Upsert content sources from ``payload`` into the database.

    Validates ``format_version`` and the top-level shape, then walks the
    ``content_sources`` array. Entries with missing or non-string
    ``repo_name`` are collected into ``ImportResult.skipped_repos``.
    Unknown keys inside a recognised entry are silently ignored — schema
    drift between environments is the whole point of having an import.

    Runtime-state fields are never touched by this function. The
    ``update_or_create`` defaults only contain operator-config fields, so
    a row that already had ``last_synced_at`` / ``last_sync_status`` /
    ``last_synced_commit`` populated keeps those values across an import.

    Raises:
        ImportError: payload is not a dict, ``format_version`` is missing
            or unsupported, or ``content_sources`` is the wrong type.
    """
    if not isinstance(payload, dict):
        raise ImportError('Content sources file must be a JSON object.')

    version = payload.get('format_version')
    if version != FORMAT_VERSION:
        raise ImportError(
            f'Unsupported format_version: {version!r}. '
            f'This build only accepts format_version={FORMAT_VERSION}.'
        )

    entries = payload.get('content_sources', [])
    if not isinstance(entries, list):
        raise ImportError('"content_sources" must be a list.')

    result = ImportResult()

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            # Skip strays like ``null`` quietly — they're not addressable
            # by ``repo_name`` so there's nothing to surface to the
            # operator. ``index`` advances normally so the human-readable
            # placeholders below stay aligned with the input.
            continue

        repo_name = entry.get('repo_name')
        if not isinstance(repo_name, str) or not repo_name:
            result.skipped_repos.append(f'<entry #{index + 1}>')
            continue

        defaults = {
            'is_private': bool(entry.get('is_private', False)),
            'max_files': _coerce_max_files(entry.get('max_files')),
        }
        # ``webhook_secret`` is special: if the key is absent we keep the
        # existing row's value (forward-compat with future exports that
        # drop secrets). When the key is present, even an empty string
        # round-trips verbatim.
        if 'webhook_secret' in entry:
            secret = entry.get('webhook_secret')
            defaults['webhook_secret'] = secret if isinstance(secret, str) else ''

        _, created = ContentSource.objects.update_or_create(
            repo_name=repo_name,
            defaults=defaults,
        )
        if created:
            result.created += 1
        else:
            result.updated += 1

    return result
