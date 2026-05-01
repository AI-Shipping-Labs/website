"""Shared helpers for hand-rendered Studio edit forms."""

from django.http import HttpResponseForbidden

SYNCED_CONTENT_POST_MESSAGE = 'This content is managed in GitHub. Edit it there.'


def parse_comma_separated_tags(raw_tags):
    """Return trimmed comma-separated tags, omitting blank entries."""
    if not raw_tags:
        return []
    return [tag.strip() for tag in raw_tags.split(',') if tag.strip()]


def reject_synced_content_post():
    """Return the standard response for source-managed content POST edits."""
    return HttpResponseForbidden(SYNCED_CONTENT_POST_MESSAGE)
