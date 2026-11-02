"""Helpers for identifying Studio actors in audit records."""


def studio_actor_label(request):
    """Return the authenticated Studio user's audit label."""
    return f"studio:{request.user.email}"
