"""Bulk-import service for the Studio contacts importer (issue #356).

The importer turns an uploaded CSV into ``User`` rows: it picks one column as
the email field, optionally appends a single tag to every row, and optionally
upgrades every row to a higher tier via a long-lived ``TierOverride``.

Splitting the parsing/upsert logic out of the view keeps the unit tests
straightforward (no request/response round-trips) and lets the view stay thin.

The whole batch runs inside ``transaction.atomic`` so a mid-import error rolls
back cleanly. Per-row issues (malformed emails, duplicates) do NOT raise --
they're collected as warnings and the import continues.
"""

import csv
import io
from dataclasses import dataclass, field

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.models import TierOverride
from accounts.utils.tags import normalize_tag
from payments.models import Tier

User = get_user_model()


# Long-lived override duration: 10 years. Effectively permanent for an import,
# but keeps the row in the same audit trail as time-limited overrides created
# from /studio/users/tier-override/ and survives Stripe webhook updates without
# clobbering ``user.tier``.
OVERRIDE_DURATION = relativedelta(years=10)

# 5 MB upload cap.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Accepted MIME types for the upload step. Browsers vary on what they send for
# .csv files (Excel often sends application/vnd.ms-excel), so we accept all of
# them and additionally fall back to the filename suffix.
ACCEPTED_CONTENT_TYPES = {
    'text/csv',
    'application/csv',
    'application/vnd.ms-excel',
}


# Sentinel used by the tier dropdown for "do not change tier".
NO_TIER_CHANGE = '__no_change__'


@dataclass
class ParsedCsv:
    """Parsed CSV payload ready to be confirmed and upserted."""

    header: list
    rows: list  # list[dict[str, str]]
    raw_text: str  # the decoded UTF-8 (or latin-1 fallback) text


@dataclass
class ImportResult:
    """Aggregate counts + warnings from a single import run."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    malformed: int = 0
    warnings: list = field(default_factory=list)  # list[(row_number, value, reason)]


def is_csv_upload(uploaded_file):
    """Return True iff the upload looks like a CSV by name or content type."""
    name = (uploaded_file.name or '').lower()
    if name.endswith('.csv'):
        return True
    return (uploaded_file.content_type or '') in ACCEPTED_CONTENT_TYPES


def decode_csv_bytes(raw_bytes):
    """Decode CSV bytes as UTF-8 with a latin-1 fallback.

    Mirrors the pattern in ``studio/views/utm_campaigns.py::utm_campaign_import``.
    """
    try:
        return raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return raw_bytes.decode('latin-1', errors='replace')


def parse_csv(raw_text):
    """Parse a CSV string into a ``ParsedCsv``.

    Returns ``(parsed, error)``: exactly one is non-None. ``error`` is a
    user-facing string suitable for the upload form.
    """
    reader = csv.reader(io.StringIO(raw_text))
    try:
        header = next(reader)
    except StopIteration:
        return None, 'CSV is empty or has no header row.'

    header = [col.strip() for col in header]
    if not any(header):
        return None, 'CSV is empty or has no header row.'

    rows = []
    for raw_row in reader:
        # Skip wholly-empty lines (csv.reader yields [] for them in some
        # dialects, [''] for others). Either way, they're noise.
        if not raw_row or all(cell == '' for cell in raw_row):
            continue
        # Pad/truncate to header length so dict access is safe.
        padded = list(raw_row) + [''] * (len(header) - len(raw_row))
        padded = padded[:len(header)]
        rows.append(dict(zip(header, padded)))

    if not rows:
        return None, 'CSV is empty or has no header row.'

    return ParsedCsv(header=header, rows=rows, raw_text=raw_text), None


def default_email_column(header):
    """Return the index in ``header`` of a column literally named "email".

    Case-insensitive, whitespace-stripped. Falls back to 0 (first column) if
    no match.
    """
    for index, column in enumerate(header):
        if column.strip().lower() == 'email':
            return index
    return 0


def _is_valid_email(value):
    """Per-row email validity: non-empty, contains @, passes ``validate_email``."""
    if not value or '@' not in value:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def run_import(parsed, *, email_column, tag, tier, granted_by):
    """Apply a CSV import to the database.

    Thin wrapper around :func:`import_contact_rows` that adapts the CSV-row
    shape (dicts keyed by header column) to the per-row shape the shared
    helper accepts. Both the Studio CSV view and the ``/api/contacts/import``
    endpoint go through ``import_contact_rows`` so the per-row upsert logic
    only lives in one place (issue #431).

    Args:
        parsed: a ``ParsedCsv`` returned by ``parse_csv``.
        email_column: the column name (header value) holding emails.
        tag: raw tag string (or empty / None for no tag).
        tier: a ``Tier`` instance, or None for "(no tier change)".
        granted_by: ``User`` who initiated the import (for ``TierOverride``).

    Returns an ``ImportResult``.
    """
    rows = [
        {"email": (row.get(email_column) or "").strip()}
        for row in parsed.rows
    ]
    return import_contact_rows(
        rows,
        default_tag=tag or "",
        default_tier=tier,
        granted_by=granted_by,
    )


def import_contact_rows(rows, *, default_tag="", default_tier=None, granted_by=None):
    """Upsert a batch of contact rows.

    Shared between the Studio CSV importer and the ``POST /api/contacts/import``
    endpoint. Each row is a dict with at least ``email``; the API also passes
    per-row ``tags`` (a list, MERGED into the user's existing tags) and
    ``tier`` (a slug applied as a long-lived ``TierOverride`` if its level
    is > 0).

    Args:
        rows: iterable of dicts. Each dict must contain ``email``; optionally
            ``tags`` (list of raw strings) and ``tier`` (Tier slug). Any other
            keys are ignored.
        default_tag: raw tag string applied to every row in the batch.
        default_tier: a ``Tier`` instance applied to every row whose tier is
            not already set per-row. Pass None to leave tiers alone.
        granted_by: ``User`` who initiated the batch (for ``TierOverride``).

    Returns an ``ImportResult``. The whole batch runs in a single
    ``transaction.atomic`` so a mid-batch failure rolls back cleanly.
    """
    result = ImportResult()
    normalized_default_tag = normalize_tag(default_tag) if default_tag else ""
    apply_default_tier = default_tier is not None and default_tier.level > 0

    # Track first-occurrence emails so subsequent duplicates within the file
    # are counted as ``skipped`` without re-running upsert logic.
    seen_emails = set()

    # Cache resolved per-row tier slugs so a 1000-row import doesn't issue
    # 1000 ``Tier.objects.get()`` queries.
    tier_cache = {}

    def _resolve_tier(slug):
        if slug not in tier_cache:
            tier_cache[slug] = Tier.objects.filter(slug=slug).first()
        return tier_cache[slug]

    with transaction.atomic():
        for index, row in enumerate(rows, start=1):
            row_number = index + 1
            raw_value = (row.get("email") or "").strip()

            if not _is_valid_email(raw_value):
                result.malformed += 1
                result.warnings.append(
                    (row_number, raw_value, "malformed email")
                )
                continue

            normalized_email = User.objects.normalize_email(raw_value).lower()
            if normalized_email in seen_emails:
                result.skipped += 1
                result.warnings.append(
                    (row_number, raw_value, "duplicate within file")
                )
                continue
            seen_emails.add(normalized_email)

            existing = User.objects.filter(email__iexact=normalized_email).first()
            if existing is not None:
                user = existing
                created_now = False
            else:
                user = User.objects.create_user(
                    email=normalized_email,
                    password=None,
                    email_verified=False,
                    unsubscribed=False,
                )
                created_now = True

            # Default tag applies to every row.
            _apply_tag(user, normalized_default_tag)
            # Per-row tags: MERGE into the user's existing tags (idempotent
            # append). The CSV importer never sets per-row tags, so only the
            # API path exercises this branch.
            for raw_tag in row.get("tags") or []:
                _apply_tag(user, normalize_tag(raw_tag))

            # Per-row tier wins over the default tier when both are set; the
            # default tier still applies when the row has no tier of its own.
            row_tier_slug = row.get("tier")
            if row_tier_slug:
                row_tier = _resolve_tier(row_tier_slug)
                if row_tier is not None and row_tier.level > 0:
                    _apply_tier_override(user, row_tier, granted_by)
            elif apply_default_tier:
                _apply_tier_override(user, default_tier, granted_by)

            if created_now:
                result.created += 1
            else:
                result.updated += 1

    return result


def _apply_tag(user, normalized_tag):
    """Append ``normalized_tag`` to ``user.tags`` if it isn't already present.

    Idempotent: a user who already carries the tag is left alone (and other
    tags on the user are preserved).
    """
    if not normalized_tag:
        return
    current = list(user.tags or [])
    if normalized_tag in current:
        return
    current.append(normalized_tag)
    user.tags = current
    user.save(update_fields=['tags'])


def _apply_tier_override(user, override_tier, granted_by):
    """Create a 10-year ``TierOverride`` and deactivate any existing active one.

    Mirrors the invariant enforced in ``studio/views/tier_overrides.py``: a
    user has at most one active override at a time.
    """
    TierOverride.objects.filter(user=user, is_active=True).update(is_active=False)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=override_tier,
        expires_at=timezone.now() + OVERRIDE_DURATION,
        granted_by=granted_by,
        is_active=True,
    )


def all_tiers_for_dropdown():
    """Return ``Tier`` rows ordered by level for the confirm-page dropdown."""
    return list(Tier.objects.order_by('level'))
