"""Shared user-import orchestration for external audience sources."""

from dataclasses import dataclass, field
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
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
                action, user = _process_row(
                    row,
                    source=source,
                    actor=actor,
                    default_tags=default_tags,
                    dry_run=dry_run,
                )
            except RowError as exc:
                batch.users_skipped += 1
                batch.errors.append(
                    {
                        "row": row_number,
                        "email": getattr(row, "email", ""),
                        "error_message": str(exc),
                    }
                )
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


def _process_row(row, *, source, actor, default_tags, dry_run):
    if not isinstance(row, ImportRow):
        raise RowError("adapter yielded a non-ImportRow value")
    if row.validation_error:
        raise RowError(row.validation_error)

    email = _normalize_email(row.email)
    tier = _resolve_tier(row.tier_slug)

    existing = User.objects.filter(email__iexact=email).first()
    action = "updated" if existing else "created"

    if dry_run:
        return action, None

    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email).first()
        if user is None:
            try:
                user = User.objects.create_user(
                    email=email,
                    password=None,
                    import_source=source,
                    imported_at=timezone.now(),
                    import_metadata=_merged_metadata({}, source, row.source_metadata),
                )
            except IntegrityError:
                user = User.objects.select_for_update().get(email__iexact=email)
                action = "updated"
        else:
            action = "updated"

        update_fields = _apply_user_updates(
            user,
            row,
            source=source,
            default_tags=default_tags,
            is_new=(action == "created"),
        )
        if update_fields:
            user.save(update_fields=sorted(update_fields))

        if tier and tier.level > 0:
            _apply_tier_override(user, tier, row.tier_expiry, actor)

    return action, user


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


def _apply_user_updates(user, row, *, source, default_tags, is_new):
    update_fields = set()
    now = timezone.now()

    if not is_new:
        if user.import_source == IMPORT_SOURCE_MANUAL:
            user.import_source = source
            update_fields.add("import_source")
        if user.imported_at is None:
            user.imported_at = now
            update_fields.add("imported_at")
        merged_metadata = _merged_metadata(user.import_metadata, source, row.source_metadata)
        if merged_metadata != (user.import_metadata or {}):
            user.import_metadata = merged_metadata
            update_fields.add("import_metadata")

    row_tags = normalize_tags([*default_tags, *(row.tags or [])])
    if row_tags:
        merged_tags = list(user.tags or [])
        for tag in row_tags:
            if tag not in merged_tags:
                merged_tags.append(tag)
        if merged_tags != (user.tags or []):
            user.tags = merged_tags
            update_fields.add("tags")

    if row.name:
        first_name, last_name = _split_name(row.name)
        if first_name and not user.first_name:
            user.first_name = first_name
            update_fields.add("first_name")
        if last_name and not user.last_name:
            user.last_name = last_name
            update_fields.add("last_name")

    for field_name, value in (row.extra_user_fields or {}).items():
        if field_name in PROTECTED_USER_FIELDS or value in (None, ""):
            continue
        try:
            User._meta.get_field(field_name)
        except FieldDoesNotExist:
            raise RowError(f"unknown user field: {field_name}") from None
        if getattr(user, field_name, None) in (None, ""):
            setattr(user, field_name, value)
            update_fields.add(field_name)

    return update_fields


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


def _apply_tier_override(user, tier, tier_expiry, actor):
    expires_at = tier_expiry or timezone.now() + LONG_LIVED_OVERRIDE_DURATION
    if TierOverride.objects.filter(
        user=user,
        override_tier=tier,
        is_active=True,
        expires_at__gt=timezone.now(),
    ).exists():
        return
    TierOverride.objects.filter(user=user, is_active=True).update(is_active=False)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=expires_at,
        granted_by=actor,
        is_active=True,
    )


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
