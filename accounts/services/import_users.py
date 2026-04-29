"""Shared user-import orchestration for external audience sources.

Identity reconciliation is intentionally conservative. Email is the only
automatic match key: importer lookups trim surrounding whitespace and lowercase
the validated email value, but ``+suffix`` aliases remain distinct addresses.
The import path never fuzzy-matches names, phone numbers, Slack IDs, Stripe
customer IDs, or other provider-specific identifiers. When multiple sources
share one email, non-empty existing values win and conflicts are logged for
staff review instead of being overwritten automatically.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import BooleanField
from django.utils import timezone
from django_q.models import Schedule

from accounts.models import (
    IMPORT_BATCH_SOURCE_CHOICES,
    IMPORT_SOURCE_MANUAL,
    ImportBatch,
    TierOverride,
)
from accounts.utils.tags import normalize_tags
from payments.models import Tier

User = get_user_model()

ADAPTERS = {}
WELCOME_TASK_PATH = "email_app.tasks.welcome_imported.enqueue_imported_welcome_email"
LONG_LIVED_OVERRIDE_DURATION = relativedelta(years=10)

PROTECTED_USER_FIELDS = {
    "id",
    "pk",
    "password",
    "email",
    "username",
    "is_staff",
    "is_superuser",
    "is_active",
    "groups",
    "user_permissions",
    "tier",
    "pending_tier",
    "import_source",
    "imported_at",
    "import_metadata",
    "tags",
}


@dataclass
class ImportRow:
    email: str
    name: str = ""
    source_metadata: dict = field(default_factory=dict)
    tier_slug: str | None = None
    tier_expiry: object | None = None
    tags: list[str] = field(default_factory=list)
    extra_user_fields: dict = field(default_factory=dict)
    validation_error: str = ""
    diagnostics: dict = field(default_factory=dict)


def register_import_adapter(source, adapter_fn):
    """Register an import adapter callable for a source."""
    _validate_source(source)
    ADAPTERS[source] = adapter_fn


def get_import_adapter(source):
    """Return the registered adapter for ``source`` or ``None``."""
    _validate_source(source)
    return ADAPTERS.get(source)


def run_import_batch(
    source,
    adapter_fn,
    *,
    dry_run=False,
    actor=None,
    default_tags=None,
    send_welcome=True,
):
    """Run one import batch against an adapter yielding ``ImportRow`` values."""
    _validate_source(source)
    batch = ImportBatch.objects.create(source=source, actor=actor, dry_run=dry_run)
    default_tags = normalize_tags(default_tags or [])
    created_user_ids = []

    try:
        for row_number, row in enumerate(adapter_fn(), start=1):
            try:
                action, user = reconcile_user(
                    row,
                    batch,
                    source=source,
                    actor=actor,
                    default_tags=default_tags,
                    dry_run=dry_run,
                    row_number=row_number,
                )
            except RowError as exc:
                batch.users_skipped += 1
                error = {
                    "row": row_number,
                    "email": getattr(row, "email", ""),
                    "error_message": str(exc),
                }
                error.update(getattr(row, "diagnostics", {}) or {})
                batch.errors.append(error)
                continue

            if action == "created":
                batch.users_created += 1
                if user and send_welcome and not dry_run and not user.unsubscribed:
                    created_user_ids.append(user.pk)
            elif action == "updated":
                batch.users_updated += 1
            else:
                batch.users_skipped += 1

        if created_user_ids:
            batch.emails_queued = queue_imported_welcome_emails(batch, created_user_ids)

        batch.status = ImportBatch.STATUS_COMPLETED
        batch.finished_at = timezone.now()
        batch.summary = _build_summary(batch)
        batch.save()
    except Exception:
        batch.status = ImportBatch.STATUS_FAILED
        batch.finished_at = timezone.now()
        batch.summary = _build_summary(batch)
        batch.save()
        raise

    return batch


def reconcile_user(
    row: ImportRow,
    batch: ImportBatch,
    *,
    source,
    actor=None,
    default_tags=None,
    dry_run=False,
    row_number=None,
):
    """Reconcile one adapter row into the canonical user for its email."""
    default_tags = normalize_tags(default_tags or [])
    email, tier = _validate_row(row)

    if dry_run:
        existing = User.objects.filter(email__iexact=email).first()
        action = "created" if existing is None else "updated"
        if existing is not None:
            branch = _branch_for_user(existing, source)
            _apply_user_updates(
                existing,
                row,
                source=source,
                default_tags=default_tags,
                branch=branch,
                batch=batch,
                row_number=row_number,
                email=email,
                dry_run=True,
            )
            if tier and tier.level > 0:
                _apply_tier_override(
                    existing,
                    tier,
                    row.tier_expiry,
                    actor,
                    batch=batch,
                    row_number=row_number,
                    email=email,
                    source=source,
                    dry_run=True,
                )
        return action, None

    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_user(
                email=email,
                password=None,
                import_source=source,
                imported_at=timezone.now(),
                import_metadata=_merged_metadata({}, source, row.source_metadata),
            )
            action = "created"
            branch = "create"
        else:
            action = "updated"
            branch = _branch_for_user(user, source)

        update_fields = _apply_user_updates(
            user,
            row,
            source=source,
            default_tags=default_tags,
            branch=branch,
            batch=batch,
            row_number=row_number,
            email=email,
            dry_run=False,
        )
        if update_fields:
            user.save(update_fields=sorted(update_fields))

        if tier and tier.level > 0:
            _apply_tier_override(
                user,
                tier,
                row.tier_expiry,
                actor,
                batch=batch,
                row_number=row_number,
                email=email,
                source=source,
                dry_run=False,
            )

    return action, user


def queue_imported_welcome_emails(batch, user_ids):
    """Create throttled one-off schedules for imported welcome emails."""
    rate = int(getattr(settings, "IMPORT_WELCOME_EMAILS_PER_HOUR", 50))
    rate = max(rate, 1)
    spacing_seconds = 3600 / rate
    now = timezone.now()
    queued = 0

    for index, user_id in enumerate(user_ids):
        Schedule.objects.create(
            name=f"welcome_email_send:{batch.pk}:{user_id}",
            func=WELCOME_TASK_PATH,
            schedule_type=Schedule.ONCE,
            repeats=1,
            next_run=now + timedelta(seconds=index * spacing_seconds),
            kwargs={"user_id": user_id},
        )
        queued += 1

    return queued


class RowError(Exception):
    """Validation error for one import row; the batch can continue."""


def _validate_row(row):
    if not isinstance(row, ImportRow):
        raise RowError("adapter yielded a non-ImportRow value")
    if row.validation_error:
        raise RowError(row.validation_error)

    email = _normalize_email(row.email)
    tier = _resolve_tier(row.tier_slug)
    _validate_extra_user_fields(row.extra_user_fields or {})
    return email, tier


def _validate_extra_user_fields(extra_user_fields):
    for field_name in extra_user_fields:
        if field_name in PROTECTED_USER_FIELDS:
            raise RowError(f"protected user field: {field_name}")
        try:
            User._meta.get_field(field_name)
        except FieldDoesNotExist:
            raise RowError(f"unknown user field: {field_name}") from None


def _branch_for_user(user, source):
    if user.import_source == source:
        return "same_source_update"
    return "cross_source_merge"


def _normalize_email(raw_email):
    email = (raw_email or "").strip()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise RowError("invalid email") from exc
    return User.objects.normalize_email(email).lower()


def _resolve_tier(tier_slug):
    if not tier_slug:
        return None
    try:
        return Tier.objects.get(slug=tier_slug)
    except Tier.DoesNotExist as exc:
        raise RowError(f"unknown tier slug: {tier_slug}") from exc


def _apply_user_updates(
    user,
    row,
    *,
    source,
    default_tags,
    branch,
    batch,
    row_number,
    email,
    dry_run,
):
    update_fields = set()
    now = timezone.now()
    is_new = branch == "create"

    if branch == "cross_source_merge" and user.import_source == IMPORT_SOURCE_MANUAL:
        if not dry_run:
            user.import_source = source
        update_fields.add("import_source")
        if user.imported_at is None:
            if not dry_run:
                user.imported_at = now
            update_fields.add("imported_at")

    if not is_new:
        merged_metadata = _merged_metadata(user.import_metadata, source, row.source_metadata)
        if merged_metadata != (user.import_metadata or {}):
            if not dry_run:
                user.import_metadata = merged_metadata
            update_fields.add("import_metadata")

    row_tags = normalize_tags([*default_tags, *(row.tags or [])])
    if row_tags:
        merged_tags = list(user.tags or [])
        for tag in row_tags:
            if tag not in merged_tags:
                merged_tags.append(tag)
        if merged_tags != (user.tags or []):
            if not dry_run:
                user.tags = merged_tags
            update_fields.add("tags")

    _apply_name_updates(
        user,
        row.name,
        update_fields,
        batch=batch,
        row_number=row_number,
        email=email,
        source=source,
        dry_run=dry_run,
        allow_overwrite=is_new,
    )
    _apply_extra_field_updates(
        user,
        row.extra_user_fields or {},
        update_fields,
        batch=batch,
        row_number=row_number,
        email=email,
        source=source,
        dry_run=dry_run,
        allow_overwrite=is_new,
    )

    return update_fields


def _apply_name_updates(
    user,
    name,
    update_fields,
    *,
    batch,
    row_number,
    email,
    source,
    dry_run,
    allow_overwrite,
):
    if not name:
        return

    first_name, last_name = _split_name(name)
    for field_name, value in (("first_name", first_name), ("last_name", last_name)):
        if not value:
            continue
        existing_value = getattr(user, field_name, "")
        if allow_overwrite or existing_value in (None, ""):
            if not dry_run:
                setattr(user, field_name, value)
            update_fields.add(field_name)
        elif existing_value != value:
            _append_conflict(
                batch,
                row_number=row_number,
                email=email,
                field=field_name,
                existing_value=existing_value,
                incoming_value=value,
                incoming_source=source,
                message=f"Existing {field_name} differs; preserving current value",
            )


def _apply_extra_field_updates(
    user,
    extra_user_fields,
    update_fields,
    *,
    batch,
    row_number,
    email,
    source,
    dry_run,
    allow_overwrite,
):
    for field_name, value in extra_user_fields.items():
        if value in (None, ""):
            continue
        existing_value = getattr(user, field_name, None)
        model_field = User._meta.get_field(field_name)
        can_fill_boolean = (
            isinstance(model_field, BooleanField)
            and existing_value is False
            and value is True
        )
        if allow_overwrite or existing_value in (None, "") or can_fill_boolean:
            if not dry_run:
                setattr(user, field_name, value)
            update_fields.add(field_name)
        elif existing_value != value:
            _append_conflict(
                batch,
                row_number=row_number,
                email=email,
                field=field_name,
                existing_value=existing_value,
                incoming_value=value,
                incoming_source=source,
                message=(
                    f"Existing {field_name} differs; possible duplicate customer "
                    "or email mismatch"
                ),
            )


def _split_name(name):
    parts = name.strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _merged_metadata(existing, source, metadata):
    existing = dict(existing or {})
    if source == "course_db":
        existing[source] = _merged_course_db_metadata(existing.get(source), metadata)
        return existing

    source_metadata = dict(existing.get(source) or {})
    source_metadata.update(metadata or {})
    existing[source] = source_metadata
    return existing


def _merged_course_db_metadata(existing, metadata):
    existing = dict(existing or {})
    metadata = dict(metadata or {})
    merged = dict(existing)

    for key in ("course_slugs", "course_db_user_ids"):
        values = list(existing.get(key) or [])
        for value in metadata.get(key) or []:
            if value not in values:
                values.append(value)
        if values:
            merged[key] = values

    dates_by_course = {
        slug: list(dates or [])
        for slug, dates in (existing.get("enrollment_dates_by_course") or {}).items()
    }
    for slug, dates in (metadata.get("enrollment_dates_by_course") or {}).items():
        values = dates_by_course.setdefault(slug, [])
        for date_value in dates or []:
            if date_value not in values:
                values.append(date_value)
    if dates_by_course:
        merged["enrollment_dates_by_course"] = dates_by_course

    return merged


def _apply_tier_override(
    user,
    tier,
    tier_expiry,
    actor,
    *,
    batch,
    row_number,
    email,
    source,
    dry_run,
):
    active_override = (
        TierOverride.objects.filter(
            user=user,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .select_related("override_tier")
        .first()
    )
    if active_override:
        if active_override.override_tier_id != tier.id:
            _append_conflict(
                batch,
                row_number=row_number,
                email=email,
                field="tier_override",
                existing_value=active_override.override_tier.slug,
                incoming_value=tier.slug,
                incoming_source=source,
                message="Existing active tier override differs; preserving current override",
            )
        return

    if dry_run:
        return

    expires_at = tier_expiry or timezone.now() + LONG_LIVED_OVERRIDE_DURATION
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=expires_at,
        granted_by=actor,
        is_active=True,
    )


def _append_conflict(
    batch,
    *,
    row_number,
    email,
    field,
    existing_value,
    incoming_value,
    incoming_source,
    message,
):
    batch.errors.append(
        {
            "kind": "conflict",
            "row": row_number,
            "email": email,
            "field": field,
            "existing_value": _json_safe_value(existing_value),
            "incoming_value": _json_safe_value(incoming_value),
            "incoming_source": incoming_source,
            "message": message,
        }
    )


def _json_safe_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _validate_source(source):
    allowed = {choice[0] for choice in IMPORT_BATCH_SOURCE_CHOICES}
    if source not in allowed:
        raise ValueError(f"Unsupported import source: {source}")


def _build_summary(batch):
    return (
        f"{batch.source} import {batch.status}: "
        f"{batch.users_created} created, {batch.users_updated} updated, "
        f"{batch.users_skipped} skipped, {batch.emails_queued} emails queued"
    )
