"""Shared primary-email lookup for operator API endpoints."""

from django.contrib.auth import get_user_model

from api.safety import error_response

User = get_user_model()


def find_user_by_primary_email(email):
    """Look up a user by case-insensitive primary email."""
    if not email:
        return None
    return (
        User.objects
        .select_related(
            "membership__tier",
            "membership__pending_tier",
            "attribution",
        )
        .filter(email__iexact=email)
        .first()
    )


def user_not_found_response():
    """Return the established operator API response for a missing user."""
    return error_response(
        "User not found",
        "user_not_found",
        status=404,
    )
