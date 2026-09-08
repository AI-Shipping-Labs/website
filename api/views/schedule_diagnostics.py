"""Staff-token diagnostics for recurring schedule reconciliation."""

from django.core.cache import caches
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import require_methods
from jobs.schedule_reconciliation import (
    SCHEDULE_RECONCILIATION_CACHE_ALIAS,
    SCHEDULE_RECONCILIATION_CACHE_KEY,
)

UNKNOWN_PAYLOAD = {
    "status": "unknown",
    "recorded_at": None,
    "last_success_at": None,
    "error": None,
    "expected_names": [],
    "missing_names": [],
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Diagnostics",
    summary="Read recurring schedule reconciliation health",
    methods={
        "GET": {
            "summary": "Read recurring schedule reconciliation health",
            "description": (
                "Returns the latest validated schedule-set reconciliation result. Staff-token only and read-only."
            ),
            "responses": {
                200: {
                    "description": "Latest recurring schedule health.",
                    "example": {
                        "status": "ok",
                        "recorded_at": "2026-09-03T10:00:00+00:00",
                        "last_success_at": "2026-09-03T10:00:00+00:00",
                        "error": None,
                        "expected_names": ["health-check", "reconcile-schedules"],
                        "missing_names": [],
                        "generated_at": "2026-09-03T10:05:00+00:00",
                    },
                },
            },
        },
    },
)
def schedule_diagnostics(request):
    payload = caches[SCHEDULE_RECONCILIATION_CACHE_ALIAS].get(
        SCHEDULE_RECONCILIATION_CACHE_KEY,
    )
    body = dict(payload) if payload is not None else dict(UNKNOWN_PAYLOAD)
    body["generated_at"] = timezone.now().isoformat()
    return JsonResponse(body, status=200)
