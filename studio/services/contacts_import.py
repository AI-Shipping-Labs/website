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
from payments.services.backfill_tiers import backfill_user_from_stripe

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


def import_contact_rows(
    rows,
    *,
    default_tag="",
    default_tier=None,
    granted_by=None,
    tier_assignment_mode="override",
):
    """Upsert a batch of contact rows.

    Shared between the Studio CSV importer and the ``POST /api/contacts/import``
    endpoint. Each row is a dict with at least ``email``; the API also passes
    per-row ``tags`` (a list, MERGED into the user's existing tags) and
    ``tier``. Studio CSV imports keep the historical ``override`` tier mode;
    API imports use ``stripe_validate`` so paid tier assignment must match
    live Stripe and never creates ``TierOverride`` rows.

    Optional per-row keys (issue #437):
        first_name / last_name -- last-write-wins on non-empty trimmed input;
            empty / whitespace-only / missing leaves existing names alone.
        stripe_customer_id / subscription_id -- write-once. Sets the field
            only if the existing User row's value is empty. If the row carries
            a non-empty value AND the user already has a different value, the
            row is NOT overwritten and a warning is appended with reason
            ``stripe_customer_id_conflict`` / ``subscription_id_conflict``.
        slack_member -- bool only. ``True`` / ``False`` writes the field and
            sets ``slack_checked_at = timezone.now()``. Omitted leaves both
            untouched. Any non-bool value is ignored and a warning is appended
            with reason ``invalid_slack_member``.

    Args:
        rows: iterable of dicts. Each dict must contain ``email``; optionally
            ``tags``, ``tier``, ``first_name``, ``last_name``,
            ``stripe_customer_id``, ``subscription_id``, ``slack_member``. Any
            other keys are ignored.
        default_tag: raw tag string applied to every row in the batch.
        default_tier: a ``Tier`` instance applied to every row whose tier is
            not already set per-row. Pass None to leave tiers alone.
        granted_by: ``User`` who initiated the batch (for ``TierOverride``).
        tier_assignment_mode: ``"override"`` or ``"stripe_validate"``.

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

            # Per-row name / Stripe / Slack writes (issue #437). These are
            # all backwards-compatible: payloads without the keys behave the
            # same as before.
            _apply_name_fields(user, row)
            _apply_write_once_id(
                user,
                row,
                row_key="stripe_customer_id",
                user_attr="stripe_customer_id",
                conflict_reason="stripe_customer_id_conflict",
                row_number=row_number,
                warnings=result.warnings,
            )
            _apply_write_once_id(
                user,
                row,
                row_key="subscription_id",
                user_attr="subscription_id",
                conflict_reason="subscription_id_conflict",
                row_number=row_number,
                warnings=result.warnings,
            )
            stripe_record = _sync_stripe_tier_after_customer_id_import(
                user,
                row,
                row_number=row_number,
                warnings=result.warnings,
            )

            # Per-row tier wins over the default tier when both are set; the
            # default tier still applies when the row has no tier of its own.
            row_tier_slug = row.get("tier")
            requested_tier = None
            if row_tier_slug:
                requested_tier = _resolve_tier(row_tier_slug)
            elif apply_default_tier:
                requested_tier = default_tier

            if requested_tier is not None and requested_tier.level > 0:
                if tier_assignment_mode == "stripe_validate":
                    _apply_stripe_validated_tier_assignment(
                        user,
                        requested_tier,
                        stripe_record=stripe_record,
                        row_number=row_number,
                        warnings=result.warnings,
                    )
                else:
                    _apply_tier_override(user, requested_tier, granted_by)
            _apply_slack_member(
                user,
                row,
                row_number=row_number,
                warnings=result.warnings,
            )

            if created_now:
                result.created += 1
            else:
                result.updated += 1

    return result


def _apply_name_fields(user, row):
    """Write ``first_name`` / ``last_name`` from ``row`` if non-empty.

    Last-write-wins on non-empty trimmed input. Empty / whitespace-only /
    missing values leave the existing field alone (issue #437).
    """
    update_fields = []
    for row_key, user_attr in (("first_name", "first_name"), ("last_name", "last_name")):
        raw = row.get(row_key)
        if not isinstance(raw, str):
            continue
        trimmed = raw.strip()
        if not trimmed:
            continue
        if getattr(user, user_attr) != trimmed:
            setattr(user, user_attr, trimmed)
            update_fields.append(user_attr)
    if update_fields:
        user.save(update_fields=update_fields)


def _apply_write_once_id(
    user, row, *, row_key, user_attr, conflict_reason, row_number, warnings,
):
    """Write a Stripe ID-style field only when the user's value is empty.

    If the row carries a non-empty value and the user already has a different
    non-empty value, the field is NOT overwritten and a warning with
    ``conflict_reason`` is appended. Identical values are a silent no-op.
    Issue #437: the Stripe webhook is the canonical writer; the import must
    never silently clobber a value already set elsewhere.
    """
    raw = row.get(row_key)
    if not isinstance(raw, str):
        return
    trimmed = raw.strip()
    if not trimmed:
        return
    current = getattr(user, user_attr) or ""
    if current == trimmed:
        return
    if current:
        warnings.append((row_number, trimmed, conflict_reason))
        return
    setattr(user, user_attr, trimmed)
    user.save(update_fields=[user_attr])


def _sync_stripe_tier_after_customer_id_import(user, row, *, row_number, warnings):
    raw = row.get("stripe_customer_id")
    if not isinstance(raw, str):
        return None
    stripe_customer_id = raw.strip()
    if not stripe_customer_id:
        return None
    if user.stripe_customer_id != stripe_customer_id:
        return None

    record = backfill_user_from_stripe(user)
    if record.status == "warning":
        warnings.append((row_number, record.message, "stripe_sync_warning"))
    return record


def _apply_stripe_validated_tier_assignment(
    user,
    requested_tier,
    *,
    stripe_record,
    row_number,
    warnings,
):
    if not user.stripe_customer_id:
        warnings.append((
            row_number,
            requested_tier.slug,
            "stripe_customer_id_required_for_tier_assignment",
        ))
        return

    record = stripe_record or backfill_user_from_stripe(user, dry_run=True)
    if record.status == "warning":
        warnings.append((row_number, record.message, "stripe_tier_validation_failed"))
        return

    stripe_tier = record.new_tier_slug or ""
    if stripe_tier != requested_tier.slug:
        warnings.append((
            row_number,
            f"requested={requested_tier.slug}; stripe={stripe_tier or 'none'}",
            "stripe_tier_mismatch",
        ))
        return

    # Only commit a non-dry-run backfill when (a) the customer-id sync above
    # didn't already commit one for this row, AND (b) the dry-run lookup
    # actually shows changes are pending. ``status == "skipped"`` means the
    # user is already on the matching tier with no metadata to refresh, so
    # the import stays idempotent and the Stripe API isn't hit twice.
    if stripe_record is None and record.status == "dry_run":
        backfill_user_from_stripe(user)


def _apply_slack_member(user, row, *, row_number, warnings):
    """Write ``slack_member`` and stamp ``slack_checked_at`` to now.

    The import is authoritative when it ships a value: the operator just
    verified the membership against Slack admin. Omitting the key leaves both
    fields untouched so the 30-min background refresher's state is preserved.
    Non-bool values (``"yes"``, ``1``, ``None``) are ignored with an
    ``invalid_slack_member`` warning instead of silently coercing.
    """
    if "slack_member" not in row:
        return
    raw = row["slack_member"]
    if not isinstance(raw, bool):
        warnings.append((row_number, raw, "invalid_slack_member"))
        return
    user.slack_member = raw
    user.slack_checked_at = timezone.now()
    user.save(update_fields=["slack_member", "slack_checked_at"])


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
