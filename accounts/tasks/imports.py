from pathlib import Path

from django.core.management.base import CommandError
from django.utils import timezone

import accounts.tasks as _tasks_pkg
from accounts.models import (
    IMPORT_SOURCE_COURSE_DB,
    IMPORT_SOURCE_SLACK,
    IMPORT_SOURCE_STRIPE,
    ImportBatch,
)
from accounts.services.import_course_db import CourseDbCsvError
from accounts.services.import_users import get_import_adapter

SCHEDULED_IMPORT_SOURCES = frozenset({IMPORT_SOURCE_SLACK, IMPORT_SOURCE_STRIPE})
SCHEDULE_NAME_BY_SOURCE = {
    IMPORT_SOURCE_SLACK: "import-slack-daily",
    IMPORT_SOURCE_STRIPE: "import-stripe-daily",
}


def run_import_batch_task(batch_id):
    """Execute a Studio-created import batch in the background worker."""
    batch = ImportBatch.objects.get(pk=batch_id)

    try:
        adapter = _adapter_for_batch(batch)
        _tasks_pkg.run_import_batch(
            batch.source,
            adapter,
            dry_run=batch.dry_run,
            actor=batch.actor,
            default_tags=batch.params.get("default_tags") or [],
            send_welcome=bool(batch.params.get("send_welcome")),
            batch=batch,
        )
    except Exception as exc:
        batch.refresh_from_db()
        batch.status = ImportBatch.STATUS_FAILED
        batch.finished_at = timezone.now()
        batch.errors = [
            *list(batch.errors or []),
            {
                "kind": "task_failure",
                "message": str(exc) or exc.__class__.__name__,
            },
        ]
        batch.summary = f"Import failed: {str(exc) or exc.__class__.__name__}"
        batch.save(update_fields=["status", "finished_at", "errors", "summary"])
        _consume_course_db_upload(batch)
        raise
    else:
        batch.refresh_from_db()
        _consume_course_db_upload(batch)


def _adapter_for_batch(batch):
    adapter = get_import_adapter(batch.source)
    if adapter is None:
        raise CommandError(f"No import adapter registered for source: {batch.source}")

    if batch.source != IMPORT_SOURCE_COURSE_DB:
        return adapter

    upload_path = batch.params.get("csv_path") or ""
    if not upload_path:
        raise CourseDbCsvError("Upload a fresh course-db CSV before running this import.")
    if not Path(upload_path).exists():
        raise CourseDbCsvError("The original course-db CSV is no longer available.")
    return adapter(csv_path=upload_path)


def _consume_course_db_upload(batch):
    if batch.source != IMPORT_SOURCE_COURSE_DB:
        return
    params = dict(batch.params or {})
    upload_path = params.get("csv_path")
    if upload_path:
        try:
            Path(upload_path).unlink(missing_ok=True)
        except OSError:
            pass
    params["csv_consumed"] = True
    params["csv_available"] = False
    batch.params = params
    batch.save(update_fields=["params"])


def run_scheduled_import(source):
    """Run a live system import for a supported recurring source."""
    if source not in SCHEDULED_IMPORT_SOURCES:
        raise ValueError(f"Scheduled import source is not supported: {source}")

    batch = ImportBatch.objects.create(
        source=source,
        actor=None,
        dry_run=False,
        status=ImportBatch.STATUS_RUNNING,
        params={
            "scheduled": True,
            "schedule_name": SCHEDULE_NAME_BY_SOURCE[source],
            "send_welcome": True,
        },
    )

    try:
        adapter = get_import_adapter(source)
        if adapter is None:
            raise CommandError(f"No import adapter registered for source: {source}")
        _tasks_pkg.run_import_batch(
            source,
            adapter,
            dry_run=False,
            actor=None,
            send_welcome=True,
            batch=batch,
        )
    except Exception as exc:
        _mark_scheduled_import_failed(batch, exc)
        return batch.pk

    return batch.pk


def _mark_scheduled_import_failed(batch, exc):
    batch.refresh_from_db()
    message = str(exc) or exc.__class__.__name__
    errors = list(batch.errors or [])
    errors.append(
        {
            "kind": "scheduled_import_failure",
            "message": message,
        }
    )
    params = dict(batch.params or {})
    params.update(
        {
            "scheduled": True,
            "schedule_name": SCHEDULE_NAME_BY_SOURCE.get(batch.source, ""),
            "send_welcome": True,
        }
    )
    batch.status = ImportBatch.STATUS_FAILED
    batch.finished_at = batch.finished_at or timezone.now()
    batch.errors = errors
    batch.summary = f"Scheduled {batch.source} import failed: {message}"
    batch.params = params
    batch.save(update_fields=["status", "finished_at", "errors", "summary", "params"])
    _maybe_send_scheduled_failure_alert(batch)


def _maybe_send_scheduled_failure_alert(batch):
    streak = []
    for candidate in ImportBatch.objects.filter(
        source=batch.source,
        dry_run=False,
        actor__isnull=True,
        params__scheduled=True,
    ).order_by("-started_at", "-pk"):
        if candidate.status == ImportBatch.STATUS_COMPLETED:
            break
        if candidate.status == ImportBatch.STATUS_FAILED:
            streak.append(candidate)

    if len(streak) != 3:
        return
    if any((candidate.params or {}).get("failure_alert_sent") for candidate in streak):
        return

    review_path = f"/studio/imports/{batch.pk}/"
    _tasks_pkg.mail_admins(
        subject=f"[AI Shipping Labs] Scheduled {batch.source} import failed 3 times",
        message=(
            f"Scheduled import source: {batch.source}\n"
            f"Latest batch id: {batch.pk}\n"
            f"Latest failure summary: {batch.summary}\n"
            f"Review: {review_path}"
        ),
        fail_silently=True,
    )
    params = dict(batch.params or {})
    params["failure_alert_sent"] = True
    batch.params = params
    batch.save(update_fields=["params"])
