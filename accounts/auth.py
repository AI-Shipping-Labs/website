"""Token authentication decorator for the JSON API (issue #431).

Reads the ``Authorization: Token <key>`` header, looks up the matching
``accounts.models.Token`` row, sets ``request.user`` to the token's owner, and
bumps ``last_used_at``. Returns a JSON 401 (NOT a redirect to /accounts/login/)
when the header is missing or the token does not exist; clients are scripts
that should never be redirected to a browser login page.
"""

from functools import wraps

from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone

from accounts.models import Token
from accounts.utils.user_checks import is_authenticated_user, is_staff_user


def token_required(view_func):
    """Require a valid ``Authorization: Token <key>`` header.

    On success: sets ``request.user`` to the token's owner and updates
    ``last_used_at`` via ``update_fields=['last_used_at']`` to avoid touching
    unrelated columns (and to skip the User.save() free-tier branch on every
    API call).

    Tokens are NOT downgraded when ``user.is_staff`` flips to False -- the
    token's owner can lose staff privileges and still authenticate, because
    revocation is the explicit way to cut API access. The Studio token-list
    page shows the owner's email so the operator can audit this.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header:
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        # Accept the literal "Token <key>" prefix. Anything else (e.g.
        # "Bearer", or a key with no scheme) is treated as missing rather
        # than invalid -- the client almost certainly forgot the scheme.
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0] != "Token" or not parts[1]:
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        key = parts[1].strip()
        token = Token.objects.filter(key=key).select_related("user").first()
        if token is None:
            return JsonResponse({"error": "Invalid token"}, status=401)
        if not is_staff_user(token.user):
            return JsonResponse({"error": "Invalid token"}, status=401)

        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])

        request.user = token.user
        # Issue #764: stash the token on the request so audit-logging views
        # (e.g. User Management API writes) can attribute the action to the
        # bearer without re-parsing the ``Authorization`` header. Existing
        # views ignore this attribute, so this is a non-breaking addition.
        request.auth_token = token
        return view_func(request, *args, **kwargs)

    return wrapper


def token_required_any_user(view_func):
    """Require a valid token row but do not require the owner to be staff.

    This is intentionally narrow. Most operator API endpoints use
    ``token_required`` and stay staff-only. A small member-owned API surface can
    use this helper, then apply its own object-level queryset permission gate.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header:
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0] != "Token" or not parts[1]:
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        key = parts[1].strip()
        token = Token.objects.filter(key=key).select_related("user").first()
        if token is None:
            return JsonResponse({"error": "Invalid token"}, status=401)

        Token.objects.filter(pk=token.pk).update(last_used_at=timezone.now())

        request.user = token.user
        request.auth_token = token
        return view_func(request, *args, **kwargs)

    return wrapper


def staff_session_or_token_required(view_func):
    """Allow EITHER a staff session OR a staff-owned API token.

    Composes ``token_required`` (staff-owned token, JSON 401 on failure)
    with the staff-session gate used by the API-docs routes (browser
    callers redirect to login when anonymous, get a flat 403 when
    authenticated but not staff).

    Dispatch rule:

    - If the request carries an ``Authorization`` header, route the
      caller through ``token_required``. That helper already enforces
      staff-owned tokens (see ``token_required`` lines 55-56) and bumps
      ``last_used_at`` on success. A missing ``Token`` scheme, an
      unknown key, or a non-staff token all surface the existing JSON
      401 shape; we never fall back to the session path because the
      client clearly intended to authenticate as an API caller and
      should not be silently redirected to a browser login page.
    - Otherwise, fall back to the staff-session gate: anonymous callers
      receive a 302 to ``LOGIN_URL`` with ``next=`` (so the browser-only
      UI flow keeps working); authenticated but non-staff callers
      receive a flat 403 (the user IS authenticated, they just lack the
      right role).

    Pick this helper for the OpenAPI spec route, where the same URL must
    serve both Swagger UI's in-browser fetch (staff session, no token)
    and external tooling like Postman or ``openapi-generator`` (token
    header, no session). Prefer ``token_required`` for pure API
    endpoints (no session path) and ``api.utils.token_or_session_required``
    for endpoints that intentionally accept any authenticated session
    plus CSRF (e.g. browser-driven mutations that share a route with
    scripted callers); that helper does NOT require staff on the
    session path, which is the wrong default for the docs surface.
    """
    token_view = token_required(view_func)

    @user_passes_test(is_authenticated_user)
    def _session_view(request, *args, **kwargs):
        if not is_staff_user(request.user):
            return HttpResponseForbidden("Staff access required.")
        return view_func(request, *args, **kwargs)

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.headers.get("Authorization"):
            return token_view(request, *args, **kwargs)
        return _session_view(request, *args, **kwargs)

    return wrapper
