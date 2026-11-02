"""Scope checks shared by member API views."""

from api.safety import error_response


def require_scope(request, scope):
    """Return a 401 response when the member key lacks ``scope``."""
    if scope in getattr(request, "member_api_scopes", set()):
        return None
    return error_response(
        "Member API key is missing the required scope",
        "insufficient_scope",
        status=401,
        details={"required_scope": scope},
    )
