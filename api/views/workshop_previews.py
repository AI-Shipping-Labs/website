"""Staff-token workshop draft preview-link operations."""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import require_methods
from content.models import Workshop
from integrations.config import site_base_url


def _payload(workshop):
    return {
        "slug": workshop.slug,
        "preview_url": (
            f'{site_base_url().rstrip("/")}{workshop.get_preview_url()}'
        ),
    }


_PATH = {
    "slug": {"type": "string", "required": True},
}
_RESPONSES = {
    200: {
        "description": "Private workshop preview URL.",
        "example": {
            "slug": "agents-in-production",
            "preview_url": "https://aishippinglabs.com/workshops/preview/123e4567-e89b-12d3-a456-426614174000",
        },
    },
    401: {"description": "Missing or invalid staff token."},
    404: {"description": "Workshop not found."},
}


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Workshops",
    methods={
        "GET": {
            "summary": "Get workshop draft preview link",
            "path_params": _PATH,
            "responses": _RESPONSES,
        },
    },
)
def workshop_preview_link(request, slug):
    workshop = get_object_or_404(Workshop, slug=slug)
    return JsonResponse(_payload(workshop))


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Workshops",
    methods={
        "POST": {
            "summary": "Regenerate workshop draft preview token",
            "description": "Rotates the private token so the old preview URL immediately returns 404.",
            "path_params": _PATH,
            "responses": _RESPONSES,
        },
    },
)
def workshop_preview_token_regenerate(request, slug):
    workshop = get_object_or_404(Workshop, slug=slug)
    workshop.regenerate_preview_token()
    return JsonResponse(_payload(workshop))
