"""Shared lookup helpers for API course endpoints."""

from content.models import Course


def get_published_course(slug):
    """Return the published course for ``slug`` or ``None``."""
    return Course.objects.filter(slug=slug, status="published").first()
