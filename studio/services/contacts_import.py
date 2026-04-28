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
    """Apply the import to the database.

    Args:
        parsed: a ``ParsedCsv`` returned by ``parse_csv``.
        email_column: the column name (header value) holding emails.
        tag: raw tag string (or empty / None for no tag).
        tier: a ``Tier`` instance, or None for "(no tier change)".
        granted_by: ``User`` who initiated the import (for ``TierOverride``).

    Returns an ``ImportResult``.
    """
    result = ImportResult()
    normalized_tag = normalize_tag(tag) if tag else ''
    # The "free" tier (level 0) is treated as a no-op for tier overrides --
    # we don't downgrade existing users via override and we don't create an
    # override that wouldn't grant any new access. Surface this as no-op
    # rather than as an error.
    apply_tier = tier is not None and tier.level > 0

    # Track first-occurrence emails so subsequent duplicates within the file
    # are counted as ``skipped`` without re-running upsert logic.
    seen_emails = set()

    with transaction.atomic():
        for index, row in enumerate(parsed.rows, start=1):
            # row_number is 1-indexed counting the header as row 1; data rows
            # therefore start at row 2.
            row_number = index + 1
            raw_value = (row.get(email_column) or '').strip()

            if not _is_valid_email(raw_value):
                result.malformed += 1
                result.warnings.append((row_number, raw_value, 'malformed email'))
                continue

            normalized_email = User.objects.normalize_email(raw_value).lower()
            if normalized_email in seen_emails:
                result.skipped += 1
                result.warnings.append(
                    (row_number, raw_value, 'duplicate within file')
                )
                continue
            seen_emails.add(normalized_email)

            existing = User.objects.filter(email__iexact=normalized_email).first()
            if existing is not None:
                _apply_tag(existing, normalized_tag)
                if apply_tier:
                    _apply_tier_override(existing, tier, granted_by)
                result.updated += 1
            else:
                user = User.objects.create_user(
                    email=normalized_email,
                    password=None,
                    email_verified=False,
                    unsubscribed=False,
                )
                _apply_tag(user, normalized_tag)
                if apply_tier:
                    _apply_tier_override(user, tier, granted_by)
                result.created += 1

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
