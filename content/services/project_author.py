"""Privacy-safe Studio project-author resolution (#1289)."""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from accounts.services.email_resolution import resolve_user_by_email


def resolve_project_author_user(project):
    """Return the authoritative/safe fallback user without mutating content."""
    if project.submitter_id is not None:
        return project.submitter

    author = (project.author or '').strip()
    if not author:
        return None
    try:
        validate_email(author)
    except ValidationError:
        return None

    user = resolve_user_by_email(author)
    if user is None or not user.is_active:
        return None
    return user
