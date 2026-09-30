"""Staff-token diagnostics for Maven removals that missed their enrollment."""

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.serializers.maven import serialize_maven_occurrence
from api.utils import require_methods
from api.views.maven_occurrences import (
    _AUTH_ERROR_SPEC,
    _METHOD_ERROR_SPEC,
    _SUMMARY_SCHEMA,
)
from integrations.services.maven_matching import split_pairs

_PAIR_SCHEMA = {
    "type": "object",
    "required": ["active", "removed"],
    "properties": {"active": _SUMMARY_SCHEMA, "removed": _SUMMARY_SCHEMA},
}


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag="Maven Integrations",
    summary="List split Maven enrollment/removal pairs",
    methods={
        "GET": {
            "description": (
                "Return every pair where a person has an ``active`` occurrence "
                "and a later ``removed`` occurrence of the same course and "
                "cohort, matched by account or normalized email plus resolved "
                "course/cohort keys (raw keys and hashes may differ). Such a "
                "removal was recorded as its own row and left the enrollment "
                "active. Repair a pair with ``POST .../occurrences/<removed "
                "id>/removal/reapply``. Read-only."
            ),
            "responses": {
                200: {
                    "description": "Split pairs, oldest removal first.",
                    "schema": {
                        "type": "object",
                        "required": ["pairs", "count"],
                        "properties": {
                            "pairs": {"type": "array", "items": _PAIR_SCHEMA},
                            "count": {"type": "integer"},
                        },
                    },
                },
                401: _AUTH_ERROR_SPEC,
                405: _METHOD_ERROR_SPEC,
            },
        }
    },
)
def maven_occurrence_split_pairs(request):
    """GET ``/api/integrations/maven/occurrences/split-pairs``."""
    now = timezone.now()
    pairs = []
    for active, removed in split_pairs():
        pairs.append(
            {
                "active": serialize_maven_occurrence(active, now=now),
                "removed": serialize_maven_occurrence(removed, now=now),
            }
        )
    return JsonResponse({"pairs": pairs, "count": len(pairs)})
