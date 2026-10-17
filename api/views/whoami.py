"""``GET /api/whoami`` -- prove the caller's staff API token.

``token_required`` already enforces a staff-owned token, so a 200 response
is itself the staff proof. The body carries only the owner email. Role flags
are deliberately not inspected or reported here: visibility gating composes
in ``api/views/_permissions.py``, and the interview-notes sweep test forbids
that attribute name in view modules.
"""

from __future__ import annotations

from django.http import JsonResponse

from accounts.auth import token_required
from api.openapi.decorator import openapi_spec


@openapi_spec(
    tag="Auth",
    summary="Show the calling staff token owner",
    description="Returns the email of the staff token owner. Used by `asl whoami`.",
    methods={
        "GET": {
            "summary": "Show the calling staff token owner",
            "responses": {
                200: {"description": "Token owner email"},
                401: {"description": "Missing or invalid token"},
            },
        },
    },
)
@token_required
def whoami(request):
    return JsonResponse({"email": getattr(request.user, "email", "")})
