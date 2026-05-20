"""Read-only worker task API endpoints (issue #714).

Three GET endpoints expose django-q ``Task`` rows to API clients
(orchestrator agents, scripts) that cannot use the Studio HTML pages:

* ``GET /api/worker/tasks/<task_id>`` -- single task detail.
* ``GET /api/worker/tasks/failed`` -- newest-first failed-task list.
* ``GET /api/worker/tasks`` -- generic list with status/group filters.

By design this surface is READ-ONLY. There is NO POST/PATCH/DELETE on
any route -- retry, delete, and drain stay in Studio HTML. The
orchestrator can inspect failures and decide what to do; it does not
need a programmatic recovery path.

Auth: every route is gated by ``@token_required`` (staff-only token).
The error/serialisation shape lives in ``api/serializers/worker.py``;
this module is just the HTTP plumbing.

Limit-clamp decision (groomed comment): ``limit=201`` is CLAMPED to
the documented maximum of 200 rather than returning 422. Rationale:
the cap is an implementation detail (DB scan size), not a contract
the caller is violating. Clamping is the same pattern the other
collection endpoints in this app already use, and it avoids
surprising agent clients that pass ``limit=1000`` to mean "give me
everything".
"""

from __future__ import annotations

from datetime import datetime

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django_q.models import Task

from accounts.auth import token_required
from api.safety import error_response
from api.serializers.worker import serialize_task_detail, serialize_task_row
from api.utils import require_methods

# Caps. ``LIMIT_MAX`` is the absolute ceiling on rows the API will
# return in one response; clients asking for more are clamped down.
LIMIT_MAX = 200
LIMIT_DEFAULT_FAILED = 20
LIMIT_DEFAULT_GENERIC = 50

VALID_STATUS_VALUES = ("success", "failed", "all")


def _parse_since(raw):
    """Parse ``since`` query param, returning ``(value, error_response)``.

    Accepts ISO-8601 datetimes including the trailing ``Z`` shorthand
    that ``datetime.fromisoformat`` only handles on Python 3.11+ (we run
    3.13 so this is safe; we still substitute ``+00:00`` defensively in
    case django-q changes the storage format under us).

    Returns ``(None, None)`` when the param is absent. Returns
    ``(None, JsonResponse)`` when the value is unparseable so callers
    can ``return error`` without rebuilding the response.
    """
    if raw is None or raw == "":
        return None, None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None, error_response(
            f"Invalid ISO-8601 datetime: {raw!r}",
            "validation_error",
            status=422,
            details={"field": "since", "value": raw},
        )
    return value, None


def _parse_limit(raw, default):
    """Parse ``limit`` query param, returning ``(value, error_response)``.

    Negative or non-integer values fail with 422. Values above
    ``LIMIT_MAX`` are clamped silently (see module docstring).
    """
    if raw is None or raw == "":
        return default, None
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


@token_required
@csrf_exempt
@require_methods("GET")
def worker_task_detail(request, task_id):
    """``GET /api/worker/tasks/<task_id>`` -- single task detail.

    404s with the canonical ``{"error": "Task not found"}`` shape rather
    than ``error_response`` because there is no machine-readable
    ``code`` operators ever switch on for "task missing" -- it's a
    terminal lookup miss.
    """
    task = Task.objects.filter(pk=task_id).first()
    if task is None:
        return JsonResponse({"error": "Task not found"}, status=404)
    return JsonResponse(serialize_task_detail(task))


@token_required
@csrf_exempt
@require_methods("GET")
def worker_tasks_failed(request):
    """``GET /api/worker/tasks/failed`` -- newest-first failed-task list.

    Mirrors the Studio "Failed Tasks" rows (``success=False``) ordered
    by ``started DESC``. ``since`` filters on ``started >= since`` so
    operators can fetch only the failures from a given deploy.
    """
    limit, err = _parse_limit(request.GET.get("limit"), LIMIT_DEFAULT_FAILED)
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err

    qs = Task.objects.filter(success=False)
    if since is not None:
        qs = qs.filter(started__gte=since)
    qs = qs.order_by("-started")[:limit]

    tasks = [serialize_task_row(t) for t in qs]
    return JsonResponse({
        "tasks": tasks,
        "count": len(tasks),
        "limit": limit,
    })


@token_required
@csrf_exempt
@require_methods("GET")
def worker_tasks_collection(request):
    """``GET /api/worker/tasks`` -- generic list across statuses.

    Default returns the most recent 50 tasks (success + failed)
    newest-first. ``status`` narrows to one bucket; ``group`` filters
    on exact ``Task.group`` match; ``since`` and ``limit`` behave the
    same as on ``/failed``.
    """
    status_value = request.GET.get("status", "all")
    if status_value not in VALID_STATUS_VALUES:
        return error_response(
            f"Invalid status value: {status_value!r}",
            "validation_error",
            status=422,
            details={
                "field": "status",
                "value": status_value,
                "allowed": list(VALID_STATUS_VALUES),
            },
        )

    limit, err = _parse_limit(
        request.GET.get("limit"),
        LIMIT_DEFAULT_GENERIC,
    )
    if err is not None:
        return err
    since, err = _parse_since(request.GET.get("since"))
    if err is not None:
        return err
    group = request.GET.get("group") or None

    qs = Task.objects.all()
    if status_value == "success":
        qs = qs.filter(success=True)
    elif status_value == "failed":
        qs = qs.filter(success=False)
    if group is not None:
        qs = qs.filter(group=group)
    if since is not None:
        qs = qs.filter(started__gte=since)
    qs = qs.order_by("-started")[:limit]

    tasks = [serialize_task_row(t) for t in qs]
    return JsonResponse({
        "tasks": tasks,
        "count": len(tasks),
        "limit": limit,
    })
