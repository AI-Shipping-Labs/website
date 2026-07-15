"""Staff-token trigger + read API for `#plan-sprints` ingest (issues #904, #925).

``POST /api/integrations/slack/plan-sprints/ingest`` kicks off the same
capture + parse + auto-apply path the daily schedule runs, but lets an
operator trigger it WITHOUT shell access. With no body it behaves like the
daily run (forward watermark / 7-day first-run default); pass ``since``
(``YYYY-MM-DD``) to retroactively read OLDER history. ``dry_run`` runs the
full path and rolls it back so the operator can preview counts before
committing.

Because a full-history backfill can be slow, the work is enqueued on the
background worker and the endpoint returns ``202`` with the task id. The
ingest is idempotent (the ``IngestedProgressEvent`` /
``AppliedProgressChange`` watermarks make re-runs safe), so a duplicate
trigger never double-applies progress.

``GET`` on the same route (issue #925) makes the ingest API-observable so
an operator can verify a backfill worked + read its counts WITHOUT a Studio
login. It returns the most recent N ``SlackChannelIngest`` runs newest-first,
each with its status, started/finished timestamps, the Slack ts window
pulled, and the run's count fields (messages seen, threads persisted,
replies added, members matched). Read-only: it never echoes Slack message
text — it surfaces only the per-run tallies + metadata the
``SlackChannelIngest`` model already stores.
"""

from datetime import date, datetime

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from community.slack_config import (
    get_slack_plan_sprints_channel_id,
    get_slack_plan_sprints_user_token,
)
from crm.models.slack_update import SlackChannelIngest
from integrations.config import is_enabled
from jobs.tasks import async_task

INGEST_TASK_PATH = "crm.tasks.ingest_plan_sprints.ingest_plan_sprints"

# GET list defaults. ``LIMIT_DEFAULT`` is the page size when ``limit`` is
# absent; ``LIMIT_MAX`` clamps oversized requests (same clamp rationale as
# ``api/views/worker.py``: the cap is a DB-scan implementation detail, not a
# contract the caller is violating).
LIMIT_DEFAULT = 10
LIMIT_MAX = 100


def _serialize_ingest_run(run):
    """Serialize one ``SlackChannelIngest`` row for the GET list.

    Exposes only per-run tallies + metadata the model already stores —
    never any Slack message text. Field names mirror the model exactly so
    the JSON is the source of truth an operator can switch on.
    """
    return {
        "id": run.id,
        "channel_id": run.channel_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "oldest_ts": run.oldest_ts,
        "latest_ts": run.latest_ts,
        "messages_seen": run.messages_seen,
        "threads_persisted": run.threads_persisted,
        "replies_added": run.replies_added,
        "members_matched": run.members_matched,
        "known_threads_checked": run.known_threads_checked,
        "advances_watermark": run.advances_watermark,
        "lease_expires_at": (
            run.lease_expires_at.isoformat() if run.lease_expires_at else None
        ),
        "error": run.error,
    }


def _parse_limit(raw):
    """Parse the ``limit`` query param, returning ``(value, error_response)``.

    Absent/empty -> the default. Non-integer or ``< 1`` -> a 422
    ``validation_error``. Values above ``LIMIT_MAX`` are clamped silently.
    """
    if raw is None or raw == "":
        return LIMIT_DEFAULT, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": "limit", "value": raw},
        )
    if value < 1:
        return None, error_response(
            "limit must be a positive integer",
            "validation_error",
            status=422,
            details={"field": "limit", "value": raw},
        )
    return min(value, LIMIT_MAX), None


def _parse_since(value):
    """Validate an optional ``since`` value. Returns ``(date|None, error|None)``."""
    if value in (None, ""):
        return None, None
    if not isinstance(value, str):
        return None, error_response(
            "since must be a YYYY-MM-DD string",
            "invalid_since",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None, error_response(
            f"since must be YYYY-MM-DD, got {value!r}",
            "invalid_since",
        )
    if parsed > date.today():
        return None, error_response(
            "since cannot be in the future",
            "invalid_since",
        )
    return parsed, None


_INGEST_RUN_EXAMPLE = {
    "id": 42,
    "channel_id": "C0123ABC",
    "status": "success",
    "started_at": "2026-06-11T04:00:00+00:00",
    "finished_at": "2026-06-11T04:00:12+00:00",
    "oldest_ts": "1748390400.000100",
    "latest_ts": "1749600000.000200",
    "messages_seen": 137,
    "threads_persisted": 24,
    "replies_added": 9,
    "members_matched": 21,
    "known_threads_checked": 12,
    "advances_watermark": True,
    "lease_expires_at": None,
    "error": "",
}

_GET_LIST_OPENAPI = {
    "summary": "List recent #plan-sprints ingest runs with counts",
    "description": (
        "Newest-first list of ``SlackChannelIngest`` runs so an operator "
        "can verify a backfill worked and read its tallies WITHOUT a Studio "
        "login. Each run carries its ``status`` (running/success/error), "
        "``started_at`` / ``finished_at``, the Slack ts window pulled "
        "(``oldest_ts`` / ``latest_ts``), and the count fields: "
        "``messages_seen``, ``threads_persisted``, ``replies_added``, "
                "``members_matched`` and ``known_threads_checked``. "
                "``advances_watermark`` distinguishes ordinary daily runs "
                "from reparse/backfill runs; ``lease_expires_at`` makes an "
                "active worker lease visible (plus ``error`` for failures). "
        "Read-only — it never echoes Slack message content, only the "
        "per-run counts/metadata the model already stores. Token-gated "
        "(staff tokens only)."
    ),
    "query": {
        "limit": {
            "type": "integer",
            "required": False,
            "description": "Page size. Default 10; clamped to 100.",
        },
    },
    "responses": {
        200: {
            "description": "Recent ingest runs, newest first.",
            "example": {
                "runs": [_INGEST_RUN_EXAMPLE],
                "count": 1,
                "limit": 10,
            },
        },
        422: {
            "description": "Invalid ``limit`` value.",
            "example": {
                "error": "Invalid integer: 'abc'",
                "code": "validation_error",
                "details": {"field": "limit", "value": "abc"},
            },
        },
    },
}


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Plan-sprints ingest",
    summary="Trigger (POST) or list (GET) #plan-sprints Slack ingest runs",
    methods={
        "GET": _GET_LIST_OPENAPI,
        "POST": {
            "summary": "Enqueue a #plan-sprints ingest run",
            "description": (
                "Kicks off the same capture + parse + auto-apply path the "
                "daily schedule runs. With no body it uses the forward "
                "watermark (or the 7-day first-run default). Pass ``since`` "
                "(``YYYY-MM-DD``, UTC midnight) to retroactively read OLDER "
                "history. ``dry_run=true`` runs the full path then rolls it "
                "back so nothing is persisted. The work runs on the "
                "background worker; the response carries the task id. The "
                "ingest is idempotent, so a duplicate trigger never "
                "double-applies progress."
            ),
            "request_body": {
                "properties": {
                    "since": {
                        "type": "string",
                        "description": "YYYY-MM-DD; read history from this date.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Run and roll back without persisting.",
                    },
                },
                "example": {"since": "2026-01-01", "dry_run": True},
            },
            "responses": {
                202: {
                    "description": "Ingest enqueued on the worker.",
                    "example": {
                        "status": "queued",
                        "task_id": "a1b2c3d4e5f6",
                        "since": "2026-01-01",
                        "dry_run": True,
                        "channel_id": "C0123ABC",
                    },
                },
                400: {
                    "description": (
                        "Invalid body or ``since`` value. Codes: "
                        "``invalid_type``, ``invalid_since``, ``invalid_dry_run``."
                    ),
                    "example": {
                        "error": "since must be YYYY-MM-DD, got 'nope'",
                        "code": "invalid_since",
                    },
                },
                409: {
                    "description": (
                        "Ingestion is not available: Slack is disabled or "
                        "the #plan-sprints channel is not configured."
                    ),
                    "example": {
                        "error": "Slack ingestion is not configured",
                        "code": "ingest_unavailable",
                    },
                },
            },
        },
    },
)
def plan_sprints_ingest(request):
    """GET (list runs) / POST (trigger) ``.../slack/plan-sprints/ingest``."""
    if request.method == "GET":
        return _list_ingest_runs(request)
    return _trigger_ingest(request)


def _list_ingest_runs(request):
    """GET — return the most recent ingest runs newest-first with counts."""
    limit, err = _parse_limit(request.GET.get("limit"))
    if err is not None:
        return err

    runs = SlackChannelIngest.objects.order_by("-started_at")[:limit]
    serialized = [_serialize_ingest_run(run) for run in runs]
    return JsonResponse(
        {
            "runs": serialized,
            "count": len(serialized),
            "limit": limit,
        },
        status=200,
    )


def _trigger_ingest(request):
    """POST — enqueue an ingest / backfill run on the worker."""
    if request.body:
        data, parse_error = parse_json_body(request)
        if parse_error is not None:
            return parse_error
        if not isinstance(data, dict):
            return error_response(
                "Body must be a JSON object",
                "invalid_type",
                details={"field": "body", "expected": "object"},
            )
    else:
        data = {}

    since, since_error = _parse_since(data.get("since"))
    if since_error is not None:
        return since_error

    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return error_response(
            "dry_run must be a boolean",
            "invalid_dry_run",
        )

    # Fail fast (without enqueuing) when the integration is not configured,
    # so the caller gets an actionable 409 instead of a silently no-op job.
    if not is_enabled("SLACK_ENABLED"):
        return error_response(
            "Slack is disabled (SLACK_ENABLED is off)",
            "ingest_unavailable",
            status=409,
        )
    channel_id = get_slack_plan_sprints_channel_id()
    if not channel_id:
        return error_response(
            "The #plan-sprints channel is not configured",
            "ingest_unavailable",
            status=409,
        )
    if not get_slack_plan_sprints_user_token():
        return error_response(
            "The #plan-sprints reply user token is not configured",
            "ingest_unavailable",
            status=409,
        )

    running = SlackChannelIngest.objects.filter(
        channel_id=channel_id,
        status="running",
        lease_expires_at__gt=timezone.now(),
    ).first()
    if running is not None:
        return error_response(
            f"A #plan-sprints ingest is already running (run {running.pk})",
            "ingest_in_progress",
            status=409,
            details={"run_id": running.pk},
        )

    task_id = async_task(
        INGEST_TASK_PATH,
        since=since,
        dry_run=dry_run,
        task_name="plan-sprints API backfill",
    )

    return JsonResponse(
        {
            "status": "queued",
            "task_id": str(task_id) if task_id else None,
            "since": since.isoformat() if since else None,
            "dry_run": dry_run,
            "channel_id": channel_id,
        },
        status=202,
    )
