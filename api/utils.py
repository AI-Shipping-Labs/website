"""HTTP helpers for the JSON API (issue #431).

Ported from the sibling ``course-management-platform/api/utils.py``. Kept
minimal on purpose: every endpoint here is JSON-in / JSON-out, no DRF.
"""

import json
from functools import wraps

from django.http import JsonResponse


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
