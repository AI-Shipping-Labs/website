"""Structured error responses for the JSON API (issue #431).

Ported from the sibling ``course-management-platform/api/safety.py``. Every
4xx response from an API endpoint goes through ``error_response`` so clients
can switch on a stable ``code`` field without parsing English error strings.
"""

from django.http import JsonResponse


def error_response(message, code, status=400, details=None):
    """Return a ``JsonResponse`` with the canonical error shape.

    Body shape::

        {"error": "<human message>", "code": "<machine code>"}

    ``details`` (optional) is included as a third top-level key only when
    explicitly provided -- omitting it on the happy path keeps the error
    payload tight.
    """
    data = {"error": message, "code": code}
    if details:
        data["details"] = details
    return JsonResponse(data, status=status)


def read_only_field_response(field, *, hint=None):
    """Return the standard validation error for an attempted read-only write."""
    details = {"field": field}
    if hint:
        details["hint"] = hint
    return error_response(
        f"{field} is read-only",
        "read_only_field",
        status=422,
        details=details,
    )
