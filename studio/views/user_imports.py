import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import get_valid_filename
from django_q.models import Schedule

from accounts.models import (
    IMPORT_BATCH_SOURCE_CHOICES,
    IMPORT_SOURCE_COURSE_DB,
    IMPORT_SOURCE_SLACK,
    IMPORT_SOURCE_STRIPE,
    ImportBatch,
)
from accounts.services.import_users import get_import_adapter
from accounts.utils.tags import normalize_tags
from jobs.tasks import async_task
from studio.decorators import staff_required

IMPORT_TASK_PATH = "accounts.tasks.run_import_batch_task"
IMPORT_SOURCES = [source for source, _label in IMPORT_BATCH_SOURCE_CHOICES]
SCHEDULED_IMPORTS = {
    IMPORT_SOURCE_SLACK: {
        "name": "import-slack-daily",
        "label": "Slack workspace",
        "cron": "0 3 * * *",
        "run_time": "03:00 UTC",
    },
    IMPORT_SOURCE_STRIPE: {
        "name": "import-stripe-daily",
        "label": "Stripe customers",
        "cron": "30 3 * * *",
        "run_time": "03:30 UTC",
    },
}
ERROR_COLUMNS = [
    "kind",
    "row",
    "email",
    "field",
    "existing_value",
    "incoming_value",
    "incoming_source",
    "message",
]


@staff_required
def import_batch_list(request):
    batches = ImportBatch.objects.select_related("actor").order_by("-started_at")
    source = request.GET.get("source") or ""
    dry_run = request.GET.get("dry_run") or ""
    if source in IMPORT_SOURCES:
        batches = batches.filter(source=source)
    if dry_run == "yes":
        batches = batches.filter(dry_run=True)
    elif dry_run == "no":
        batches = batches.filter(dry_run=False)

    page_obj = Paginator(batches, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "studio/imports/list.html",
        {
            "page_obj": page_obj,
            "source_filter": source,
            "dry_run_filter": dry_run,
            "source_options": IMPORT_BATCH_SOURCE_CHOICES,
            "scheduled_imports": _scheduled_import_context(),
        },
    )


@staff_required
def import_batch_detail(request, batch_id):
    batch = get_object_or_404(ImportBatch.objects.select_related("actor"), pk=batch_id)
    return render(request, "studio/imports/detail.html", _detail_context(batch))


@staff_required
def import_batch_fragment(request, batch_id):
    batch = get_object_or_404(ImportBatch.objects.select_related("actor"), pk=batch_id)
    return render(request, "studio/imports/_status.html", _detail_context(batch))


@staff_required
def import_batch_new(request):
    if request.method != "POST":
        return render(request, "studio/imports/new.html", _new_context())

    source = request.POST.get("source") or ""
    dry_run = request.POST.get("dry_run") == "on"
    if source not in IMPORT_SOURCES:
        return render(
            request,
            "studio/imports/new.html",
            _new_context(error="Choose a supported import source.", posted=request.POST),
            status=400,
        )
    if get_import_adapter(source) is None:
        return render(
            request,
            "studio/imports/new.html",
            _new_context(error="That import adapter is not available yet.", posted=request.POST),
            status=400,
        )
    if not dry_run and not request.user.is_superuser:
        return HttpResponseForbidden("Live imports are restricted to superusers.")

    uploaded_file = request.FILES.get("csv_file")
    if source == IMPORT_SOURCE_COURSE_DB and uploaded_file is None:
        return render(
            request,
            "studio/imports/new.html",
            _new_context(error="Upload a course-db CSV file.", posted=request.POST),
            status=400,
        )
    if uploaded_file is not None and not uploaded_file.name.lower().endswith(".csv"):
        return render(
            request,
            "studio/imports/new.html",
            _new_context(error="Only .csv uploads are supported.", posted=request.POST),
            status=400,
        )
    if source != IMPORT_SOURCE_COURSE_DB and uploaded_file is not None:
        return render(
            request,
            "studio/imports/new.html",
            _new_context(error="CSV uploads are only supported for course-db imports.", posted=request.POST),
            status=400,
        )

    default_tags = normalize_tags(_split_tags(request.POST.get("tags", "")))
    send_welcome = False if dry_run else request.POST.get("send_welcome") == "on"
    batch = ImportBatch.objects.create(
        source=source,
        actor=request.user,
        dry_run=dry_run,
        status=ImportBatch.STATUS_RUNNING,
        params={
            "default_tags": default_tags,
            "send_welcome": send_welcome,
        },
    )
    if uploaded_file is not None:
        batch.params = {
            **batch.params,
            **_store_course_db_upload(batch, uploaded_file),
        }
        batch.save(update_fields=["params"])

    async_task(IMPORT_TASK_PATH, batch.pk)
    messages.success(request, f"Import batch {batch.pk} was queued.")
    return redirect("studio_import_batch_detail", batch_id=batch.pk)


@staff_required
def import_batch_rerun(request, batch_id):
    if request.method != "POST":
        return redirect("studio_import_batch_detail", batch_id=batch_id)
    if not request.user.is_superuser:
        return HttpResponseForbidden("Live reruns are restricted to superusers.")

    original = get_object_or_404(ImportBatch, pk=batch_id)
    if original.dry_run or original.status not in {
        ImportBatch.STATUS_COMPLETED,
        ImportBatch.STATUS_FAILED,
    }:
        messages.error(request, "Only completed or failed live imports can be run again.")
        return redirect("studio_import_batch_detail", batch_id=original.pk)

    params = _safe_rerun_params(original)
    if original.source == IMPORT_SOURCE_COURSE_DB and not params.get("csv_available"):
        messages.error(
            request,
            "The original course-db CSV is no longer available. Start a new import and upload the CSV again.",
        )
        return redirect("studio_import_batch_new")

    rerun = ImportBatch.objects.create(
        source=original.source,
        actor=request.user,
        dry_run=False,
        status=ImportBatch.STATUS_RUNNING,
        params=params,
    )
    async_task(IMPORT_TASK_PATH, rerun.pk)
    messages.success(request, f"Import batch {rerun.pk} was queued.")
    return redirect("studio_import_batch_detail", batch_id=rerun.pk)


@staff_required
def import_schedule_toggle(request, source):
    if request.method != "POST":
        return redirect("studio_import_batch_list")
    if not request.user.is_superuser:
        return HttpResponseForbidden("Schedule changes are restricted to superusers.")
    if source not in SCHEDULED_IMPORTS:
        messages.error(request, "Choose a supported scheduled import source.")
        return redirect("studio_import_batch_list")

    action = request.POST.get("action")
    if action not in {"enable", "disable"}:
        messages.error(request, "Choose enable or disable.")
        return redirect("studio_import_batch_list")

    schedule_config = SCHEDULED_IMPORTS[source]
    with transaction.atomic():
        schedule = get_object_or_404(
            Schedule.objects.select_for_update(),
            name=schedule_config["name"],
        )
        schedule.repeats = -1 if action == "enable" else 0
        schedule.save(update_fields=["repeats"])

    verb = "enabled" if action == "enable" else "disabled"
    messages.success(request, f"{schedule_config['label']} daily import {verb}.")
    return redirect("studio_import_batch_list")


def _detail_context(batch):
    normalized_errors = _normalized_errors(batch.errors or [])
    params_json = json.dumps(batch.params or {}, indent=2, sort_keys=True)
    return {
        "batch": batch,
        "params_json": params_json,
        "errors": normalized_errors,
        "error_columns": ERROR_COLUMNS,
        "can_rerun": (
            not batch.dry_run
            and batch.status in {ImportBatch.STATUS_COMPLETED, ImportBatch.STATUS_FAILED}
        ),
        "course_db_upload_missing": (
            batch.source == IMPORT_SOURCE_COURSE_DB
            and not (batch.params or {}).get("csv_available")
        ),
    }


def _new_context(*, error="", posted=None):
    return {
        "error": error,
        "source_options": [
            {
                "value": source,
                "label": label,
                "available": get_import_adapter(source) is not None,
            }
            for source, label in IMPORT_BATCH_SOURCE_CHOICES
        ],
        "posted": posted or {},
    }


def _split_tags(raw_tags):
    return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]


def _store_course_db_upload(batch, uploaded_file):
    upload_dir = Path(settings.BASE_DIR) / "tmp" / "studio_import_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = get_valid_filename(uploaded_file.name)
    path = upload_dir / f"batch-{batch.pk}-{filename}"
    with path.open("wb") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)
    return {
        "csv_path": str(path),
        "csv_original_filename": uploaded_file.name,
        "csv_available": True,
        "csv_consumed": False,
    }


def _safe_rerun_params(batch):
    params = batch.params or {}
    safe = {
        "default_tags": params.get("default_tags") or [],
        "send_welcome": bool(params.get("send_welcome")),
    }
    if batch.source == IMPORT_SOURCE_COURSE_DB:
        csv_path = params.get("csv_path") or ""
        safe.update(
            {
                "csv_path": csv_path,
                "csv_original_filename": params.get("csv_original_filename", ""),
                "csv_available": bool(csv_path and Path(csv_path).exists()),
                "csv_consumed": False,
            }
        )
    return safe


def _scheduled_import_context():
    schedules = {
        schedule.name: schedule
        for schedule in Schedule.objects.filter(
            name__in=[config["name"] for config in SCHEDULED_IMPORTS.values()]
        )
    }
    items = []
    for source, config in SCHEDULED_IMPORTS.items():
        schedule = schedules.get(config["name"])
        items.append(
            {
                "source": source,
                **config,
                "schedule": schedule,
                "enabled": bool(schedule and schedule.repeats != 0),
            }
        )
    return items


def _normalized_errors(errors):
    normalized = []
    for error in errors:
        if not isinstance(error, dict):
            error = {"message": str(error)}
        row = {}
        for column in ERROR_COLUMNS:
            row[column] = error.get(column) or ""
        if not row["message"]:
            row["message"] = error.get("error_message") or error.get("detail") or ""
        normalized.append(row)
    return normalized
