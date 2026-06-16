"""HTTP helpers for the JSON API (issue #431).

Ported from the sibling ``course-management-platform/api/utils.py``. Kept
minimal on purpose: every endpoint here is JSON-in / JSON-out, no DRF.
"""

import json
from functools import wraps

from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware

from accounts.auth import token_required
from api.safety import error_response


def body_must_be_object_response(*, status=400):
    """Return the canonical response for endpoints expecting object bodies."""
    return error_response(
        "Body must be a JSON object",
        "invalid_type",
        status=status,
        details={"field": "body", "expected": "object"},
    )


def validation_response(details, message="Validation error"):
    """Return the canonical 422 validation-error response."""
    return error_response(
        message,
        "validation_error",
        status=422,
        details=details,
    )


def coerce_optional_text(value):
    """Normalize optional text payload values for JSON API writes."""
    if value is None:
        return ""
    return str(value).strip()


def parse_bool_query(value):
    """Parse a tolerant boolean query-string value.

    Missing or invalid values return ``None`` so callers can preserve their
    endpoint-specific validation or default behavior.
    """
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return None


def delete_not_available_response(message, code):
    """Return the canonical structured 405 for API resources with no DELETE."""
    return error_response(message, code, status=405)


def parse_json_body(request):
    """Parse JSON body from ``request``, returning ``(data, error_response)``.

    Exactly one of the two return values is non-None. The error is a ready-to-
    return ``JsonResponse`` with status 400 and the canonical
    ``{"error": "Invalid JSON"}`` body so callers don't have to repeat the
    shape on every endpoint.
    """
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({"error": "Invalid JSON"}, status=400)


def require_methods(*methods):
    """Decorator restricting the allowed HTTP methods.

    Returns ``405`` with ``{"error": "Method not allowed"}`` for any other
    method. Decorator order matters: keep ``token_required`` outermost so 401s
    fire before 405s (an unauthenticated client should not be told what
    methods the endpoint accepts).
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return JsonResponse(
                    {"error": "Method not allowed"},
                    status=405,
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def token_or_session_required(view_func):
    """Allow staff/operator token auth or logged-in browser session auth.

    API clients keep using ``Authorization: Token ...`` and bypass CSRF as
    before. Browser calls without an Authorization header must come from an
    authenticated session and pass Django's normal CSRF check.
    """
    token_view = token_required(view_func)
    csrf_checker = CsrfViewMiddleware(lambda request: None)

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.headers.get("Authorization"):
            return token_view(request, *args, **kwargs)
        if not getattr(request.user, "is_authenticated", False):
            return JsonResponse({"error": "Authentication required"}, status=401)

        def csrf_checked_callback(request, *args, **kwargs):
            return None

        failure_response = csrf_checker.process_view(
            request,
            csrf_checked_callback,
            args,
            kwargs,
        )
        if failure_response is not None:
            return failure_response
        return view_func(request, *args, **kwargs)

    wrapper.csrf_exempt = True
    return wrapper
