"""Read-only staff API for sanitized durable-job failure counts."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import require_methods
from jobs.dead_intent_summary import dead_intent_summary


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Worker Tasks",
    summary="Summarize terminal dead durable job intents",
    methods={"GET": {
        "description": (
            "Read-only counts by registered handler and allowlisted error category. "
            "Unknown labels collapse to safe categories; registered-handler overflow "
            "uses other_registered_handler. All counts reconcile to total_dead. "
            "Timestamps describe intent creation. Dead intents remain terminal with "
            "retained_dead disposition and no_automatic_retry policy. "
            "No intent identifiers, payloads, keys or raw errors are returned."
        ),
        "responses": {200: {"description": "Sanitized dead-intent summary."}},
    }},
)
def worker_intents_dead_summary(request):
    return JsonResponse(dead_intent_summary())
