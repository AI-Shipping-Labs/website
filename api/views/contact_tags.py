"""Staff-token contact-tag namespace API."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.utils.tags import rename_tag, tags_with_user_counts
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Contact tags",
    summary="List the in-use contact-tag namespace",
    methods={
        "GET": {
            "summary": "List contact tags with user counts",
            "responses": {
                200: {
                    "description": "Sorted in-use contact tags and carrier counts.",
                    "example": {
                        "tags": [{"name": "paid", "user_count": 2}],
                        "count": 1,
                    },
                },
                401: {"description": "Missing, invalid, or non-staff token."},
            },
        },
    },
)
def contact_tags_collection(request):
    tags = tags_with_user_counts()
    return JsonResponse({"tags": tags, "count": len(tags)})


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Contact tags",
    summary="Rename a contact tag across all users",
    methods={
        "POST": {
            "summary": "Rename one contact tag",
            "request_body": {
                "required": ["new"],
                "properties": {"new": {"type": "string"}},
                "example": {"new": "paid"},
            },
            "responses": {
                200: {
                    "description": "Normalized names and affected-user count.",
                    "example": {
                        "old": "paid-user",
                        "new": "paid",
                        "affected": 2,
                    },
                },
                401: {"description": "Missing, invalid, or non-staff token."},
                422: {"description": "The new name normalizes to empty."},
            },
        },
    },
)
def contact_tag_rename(request, name):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    new_name = data.get("new") if isinstance(data, dict) else None
    try:
        result = rename_tag(name, new_name)
    except ValueError as exc:
        return error_response(
            str(exc),
            "invalid_tag",
            status=422,
            details={"field": "new"},
        )
    return JsonResponse(result)
