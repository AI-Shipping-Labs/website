"""Member API documentation views."""

from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.auth import member_api_key_required

MEMBER_API_USAGE_GUIDE_URL = (
    "https://github.com/AI-Shipping-Labs/website/blob/main/"
    "docs/member-api/plans.md"
)


@require_GET
@login_required
def docs_page(request):
    return render(
        request,
        "member_api/docs.html",
        {"member_api_usage_guide_url": MEMBER_API_USAGE_GUIDE_URL},
    )


@require_GET
def openapi_json(request):
    if request.headers.get("Authorization"):
        return member_api_key_required("plans:read")(_openapi_json)(request)
    if not getattr(request.user, "is_authenticated", False):
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    return _openapi_json(request)


def _openapi_json(request):
    path = Path(settings.BASE_DIR) / "_docs" / "member-openapi.json"
    if not path.exists():
        return HttpResponse(
            "Member OpenAPI spec not generated. Run "
            "``python manage.py generate_member_openapi``.",
            status=500,
            content_type="text/plain",
        )
    return HttpResponse(path.read_bytes(), content_type="application/json")
