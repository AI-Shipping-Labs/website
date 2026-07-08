"""Boot-timing diagnostics endpoint (issue #1142).

One narrow, staff-token, read-only ``GET`` endpoint that returns the most
recent per-phase container cold-start timings for both the web and worker
tiers. Phase 1 of #1141 added ``BOOT_TIMING phase=<name> seconds=<float>``
logging to ``scripts/entrypoint_init.py``, but those numbers only reach
stdout -> CloudWatch, which is inaccessible from where we operate. This
endpoint reads the same numbers that the entrypoint now persists to the
shared ``django_q`` cache so an operator can ``curl`` them with a staff
token and prioritise #1141 Phase 2 from real data.

``GET /api/diagnostics/boot-timing`` -- read-only. ``@token_required``
(staff-owned tokens only; non-staff/invalid/missing -> 401),
``@require_methods("GET")`` (any other method -> 405). Mirrors the auth /
method model of ``api/views/cleanup_gates.py:cleanup_gates_diagnostics``
exactly.

Store contract
--------------
The entrypoint writes one payload per role to the shared ``django_q``
``DatabaseCache`` (backed by the ``django_q_cache`` table) under keys
``boot_timing:web`` / ``boot_timing:worker``. The ``default`` cache is a
per-process ``LocMemCache`` and cannot carry the worker container's
numbers to the web endpoint, so the cross-container ``django_q`` cache is
used. When a tier's key is absent (no boot has written it yet, or the
first-ever-deploy race left the worker key unwritten once), that tier's
value is ``null``; the response is still a clean 200 with ``generated_at``.
"""

from django.core.cache import caches
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import require_methods

# The cross-container cache alias (see ``website/settings.py`` CACHES). The
# entrypoint writes ``boot_timing:<role>`` keys here; this endpoint reads
# them back.
BOOT_TIMING_CACHE_ALIAS = "django_q"

# Ordered role -> cache-key mapping. The response body carries one entry per
# role (the persisted payload or ``null``), then a top-level ``generated_at``.
BOOT_TIMING_KEYS = {
    "web": "boot_timing:web",
    "worker": "boot_timing:worker",
}


_RESPONSE_EXAMPLE = {
    "web": {
        "tag": "20260708-ab12cd3",
        "recorded_at": "2026-07-08T10:00:00+00:00",
        "role": "web",
        "phases": {
            "django_setup": 4.2,
            "migrate": 8.1,
            "check": 1.3,
            "setup_schedules": 0.4,
            "total": 14.0,
        },
    },
    "worker": {
        "tag": "20260708-ab12cd3",
        "recorded_at": "2026-07-08T10:00:05+00:00",
        "role": "worker",
        "phases": {
            "django_setup": 4.1,
            "check": 1.2,
            "setup_schedules": 0.4,
            "total": 5.9,
        },
    },
    "generated_at": "2026-07-08T10:05:00+00:00",
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Diagnostics",
    summary="Read container boot timings",
    methods={
        "GET": {
            "summary": "Read the latest per-phase container boot timings",
            "description": (
                "Returns the most recent per-phase cold-start timings for "
                "both the web and worker container tiers, each with its "
                "build ``tag``, ``recorded_at``, ``role``, and a ``phases`` "
                "map, plus a top-level ``generated_at``. Staff-token only, "
                "read-only. A tier with no captured boot yet is ``null``; "
                "the response is still a clean 200 (never 404/500)."
            ),
            "responses": {
                200: {
                    "description": "The latest web and worker boot timings.",
                    "example": _RESPONSE_EXAMPLE,
                },
            },
        },
    },
)
def boot_timing_diagnostics(request):
    """``GET /api/diagnostics/boot-timing`` -- read-only boot timings."""
    cache = caches[BOOT_TIMING_CACHE_ALIAS]
    body = {role: cache.get(key) for role, key in BOOT_TIMING_KEYS.items()}
    body["generated_at"] = timezone.now().isoformat()
    return JsonResponse(body, status=200)
