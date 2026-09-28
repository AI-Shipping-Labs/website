"""Stable UUID-backed public links for content detail pages."""

from urllib.parse import urlsplit, urlunsplit

from django.http import Http404, HttpResponseRedirect
from django.views.decorators.http import require_safe

from content.services.content_lookup import (
    find_content_matches,
    is_public_content,
)


def _find_share_target(content_id):
    """Return the sole row using ``content_id`` or 404 on absent/ambiguous IDs."""
    matches = find_content_matches(content_id, limit=2)
    if len(matches) > 1:
        raise Http404('Content share ID is ambiguous.')
    if not matches:
        raise Http404('Content share ID was not found.')
    return matches[0]


def _local_destination(target, query_string):
    destination = target.get_absolute_url()
    parts = urlsplit(destination)
    if (
        parts.scheme
        or parts.netloc
        or not parts.path.startswith('/')
        or parts.path.startswith('//')
        or '\\' in parts.path
        or parts.fragment
    ):
        raise Http404('Content does not have a local public destination.')

    return urlunsplit(('', '', parts.path, query_string or parts.query, ''))


@require_safe
def content_share_link(request, content_id):
    """Redirect GET/HEAD share links to the target's current canonical URL."""
    target = _find_share_target(content_id)
    if not is_public_content(target):
        raise Http404('Content is not published.')

    location = _local_destination(
        target,
        request.META.get('QUERY_STRING', ''),
    )
    return HttpResponseRedirect(location)


def invalid_content_share_link(request, **kwargs):
    """Keep malformed or slash-suffixed ``/c`` paths out of the CMS fallback."""
    raise Http404('Content share link was not found.')
