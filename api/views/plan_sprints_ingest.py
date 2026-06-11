"""Staff-token trigger for a `#plan-sprints` ingest / backfill (issue #904).

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
"""

from datetime import date, datetime

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from community.slack_config import get_slack_plan_sprints_channel_id
from integrations.config import is_enabled
from jobs.tasks import async_task

INGEST_TASK_PATH = "crm.tasks.ingest_plan_sprints.ingest_plan_sprints"


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


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Plan-sprints ingest",
    summary="Trigger a #plan-sprints Slack ingest / retroactive backfill",
    methods={
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
    """POST ``/api/integrations/slack/plan-sprints/ingest``."""
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
