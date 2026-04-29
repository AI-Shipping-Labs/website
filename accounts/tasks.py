from pathlib import Path

from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import IMPORT_SOURCE_COURSE_DB, ImportBatch
from accounts.services.import_course_db import CourseDbCsvError
from accounts.services.import_users import get_import_adapter, run_import_batch


def run_import_batch_task(batch_id):
    """Execute a Studio-created import batch in the background worker."""
    batch = ImportBatch.objects.get(pk=batch_id)

    try:
        adapter = _adapter_for_batch(batch)
        run_import_batch(
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
