"""Cleanup-gate diagnostics endpoint (issue #1087).

One narrow, staff-token, read-only ``GET`` endpoint that returns the
production-data gate counts the blocked cleanup issues (#1016 / #1018 /
#1017) wait on. Each gate is gated on a count being zero; the count must
come from the authenticated production API (no local SQLite, no prod DB
tunnel, no fixture assumptions), and that count was not exposed before
this endpoint.

``GET /api/diagnostics/cleanup-gates`` -- read-only. ``@token_required``
(staff-owned tokens only; non-staff/invalid/missing -> 401),
``@require_methods("GET")`` (any other method -> 405). Returns integer
counts only -- no PII, no row ids, no titles -- plus a ``generated_at``
timestamp so a reader can record exactly when the count was taken.

Extensibility contract
----------------------
``GATE_COUNTS`` is a module-level ordered mapping of gate name -> a
zero-arg callable returning the count. The view iterates the mapping to
build the response body, then appends ``generated_at``. Adding a future
cleanup gate is a SINGLE addition here: write a ``_count_*`` callable and
add one entry to ``GATE_COUNTS`` (plus the OpenAPI example below). No
change to auth, routing, or response assembly is required.
"""

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import require_methods
from content.models import UserCourseProgress, Workshop
from events.models import Event


def _count_null_completed_unit_progress():
    """Legacy null-completed Unit progress rows (gate for #1016 / A1).

    The ``unit__isnull=False`` clause is kept verbatim so the reported
    gate matches #1016's stated check exactly. ``UserCourseProgress.unit``
    is a non-nullable FK, so that clause is always true and harmless; the
    meaningful predicate is ``completed_at__isnull=True``.
    """
    return UserCourseProgress.objects.filter(
        unit__isnull=False,
        completed_at__isnull=True,
    ).count()


def _count_workshops_missing_content_id():
    """Workshops missing their stable content id (gate for #1018 / A8).

    ``Workshop.content_id`` is ``UUIDField(unique=True, null=True,
    blank=True)`` (from ``SyncedContentIdentityMixin``); a null value
    means the workshop has no stable content id.
    """
    return Workshop.objects.filter(content_id__isnull=True).count()


def _count_completed_future_events():
    """Completed events whose start is still in the future (gate for #1017 / A3).

    Counts events marked ``status="completed"`` whose ``start_datetime``
    is after timezone-aware ``timezone.now()`` evaluated at request time
    -- the stale-status workaround #1017 removes.
    """
    return Event.objects.filter(
        status="completed",
        start_datetime__gt=timezone.now(),
    ).count()


# Ordered mapping of gate name -> zero-arg count callable. See the module
# docstring for the one-line-extension contract.
GATE_COUNTS = {
    "null_completed_unit_progress": _count_null_completed_unit_progress,
    "workshops_missing_content_id": _count_workshops_missing_content_id,
    "completed_future_events": _count_completed_future_events,
}


_RESPONSE_EXAMPLE = {
    "null_completed_unit_progress": 0,
    "workshops_missing_content_id": 0,
    "completed_future_events": 0,
    "generated_at": "2026-06-26T10:00:00+00:00",
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Diagnostics",
    summary="Read cleanup-gate counts",
    methods={
        "GET": {
            "summary": "Read the cleanup-gate counts",
            "description": (
                "Returns the production-data gate counts the blocked "
                "cleanup issues (#1016 / #1018 / #1017) wait on, plus a "
                "``generated_at`` timestamp. Staff-token only, read-only. "
                "Integer counts only -- no PII, no row ids, no titles. "
                "Each gate proceeds only when its count is ``0``."
            ),
            "responses": {
                200: {
                    "description": "The cleanup-gate counts.",
                    "example": _RESPONSE_EXAMPLE,
                },
            },
        },
    },
)
def cleanup_gates_diagnostics(request):
    """``GET /api/diagnostics/cleanup-gates`` -- read-only gate counts."""
    body = {name: count() for name, count in GATE_COUNTS.items()}
    body["generated_at"] = timezone.now().isoformat()
    return JsonResponse(body, status=200)
