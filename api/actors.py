"""Helpers for identifying authenticated API callers."""


def actor_label(request):
    """Return the calling token's name or masked prefix."""
    token = getattr(request, "auth_token", None)
    if token is None:
        return "unknown"
    return token.name or token.key_prefix
