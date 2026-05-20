"""Documentation views: serve the OpenAPI JSON and the Swagger UI page.

Both views are staff-only via session auth (``user_passes_test`` redirects
anonymous users to ``/accounts/login/``; authenticated non-staff get a flat
403 from the same wrapper). The routes deliberately live outside the
``token_required`` surface that the rest of ``api/views/*`` uses, because a
fresh operator pulling up ``/api/docs`` in a browser does not yet have a
token to authorize the page with. The Swagger UI page then prompts the
operator for a token and uses it to call the documented endpoints.

These two routes are excluded from the generated spec itself (see
``api.openapi.builder._DOCS_ROUTE_NAMES``) -- the documentation does not
document itself.
"""

from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_GET


def _staff_required(view_func):
    """Allow staff sessions; 403 for authenticated non-staff; redirect anon to login.

    ``user_passes_test`` already handles the anon-redirect case by sending
    a 302 to ``LOGIN_URL`` with a ``next=`` param. For authenticated but
    non-staff users it redirects to the same login page -- but we want a
    flat 403 instead because the user IS authenticated, they just lack
    the right role. We layer a small wrapper to convert that case.
    """

    @user_passes_test(lambda u: getattr(u, "is_authenticated", False))
    def _authenticated_only(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required.")
        return view_func(request, *args, **kwargs)

    return _authenticated_only


@require_GET
@_staff_required
def openapi_json(request):
    """Serve ``_docs/openapi.json`` as ``application/json``.

    Reads from disk on each request rather than building the spec
    in-process -- the committed file IS the canonical artefact, and
    serving it directly guarantees Swagger UI sees the same bytes that
    drift-checks are run against.
    """
    path = Path(settings.BASE_DIR) / "_docs" / "openapi.json"
    if not path.exists():
        return HttpResponse(
            "OpenAPI spec not generated. Run ``python manage.py generate_openapi``.",
            status=500,
            content_type="text/plain",
        )
    return HttpResponse(
        path.read_bytes(),
        content_type="application/json",
    )


@require_GET
@_staff_required
def docs_page(request):
    """Render the Swagger UI page at ``/api/docs``.

    Pulls swagger-ui-dist from a pinned CDN URL; the page itself is a
    small wrapper that points the SwaggerUIBundle at our ``/api/openapi.json``
    endpoint above.
    """
    return render(request, "api/docs.html")
