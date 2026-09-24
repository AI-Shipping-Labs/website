"""Stable UUID-backed public links for content detail pages."""

from urllib.parse import urlsplit, urlunsplit

from django.http import Http404, HttpResponseRedirect
from django.views.decorators.http import require_safe

from content.models import (
    Article,
    Course,
    Download,
    MarketingPage,
    Module,
    Project,
    Tutorial,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.models.marketing_page import STATUS_PUBLISHED
from events.models import PUBLIC_EVENT_STATUSES, Event

SHAREABLE_MODELS = (
    (Article, 'content_id', ()),
    (Project, 'content_id', ()),
    (Tutorial, 'content_id', ()),
    (Download, 'content_id', ()),
    (Workshop, 'content_id', ()),
    (WorkshopPage, 'content_id', ('workshop',)),
    (MarketingPage, 'content_id', ()),
    (Event, 'content_id', ()),
    (Course, 'source_content_id', ()),
    (Module, 'source_content_id', ('course', 'parent')),
    (Unit, 'source_content_id', ('module__course', 'module__parent')),
)


def _find_share_target(content_id):
    """Return the sole row using ``content_id`` or 404 on absent/ambiguous IDs."""
    matches = []
    for model, field_name, related_fields in SHAREABLE_MODELS:
        queryset = model.objects.filter(**{field_name: content_id}).order_by('pk')
        if related_fields:
            queryset = queryset.select_related(*related_fields)
        matches.extend(queryset[:2 - len(matches)])
        if len(matches) > 1:
            raise Http404('Content share ID is ambiguous.')

    if not matches:
        raise Http404('Content share ID was not found.')
    return matches[0]


def _is_public_target(target):
    if isinstance(target, (Article, Project, Tutorial, Download)):
        return target.published
    if isinstance(target, Workshop):
        return target.status == STATUS_PUBLISHED
    if isinstance(target, WorkshopPage):
        return target.workshop.status == STATUS_PUBLISHED
    if isinstance(target, MarketingPage):
        return target.status == STATUS_PUBLISHED
    if isinstance(target, Event):
        return target.published and target.status in PUBLIC_EVENT_STATUSES
    if isinstance(target, Course):
        return target.status == STATUS_PUBLISHED
    if isinstance(target, Module):
        return target.course.status == STATUS_PUBLISHED
    if isinstance(target, Unit):
        return target.module.course.status == STATUS_PUBLISHED
    return False


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
    if not _is_public_target(target):
        raise Http404('Content is not published.')

    location = _local_destination(
        target,
        request.META.get('QUERY_STRING', ''),
    )
    return HttpResponseRedirect(location)


def invalid_content_share_link(request, **kwargs):
    """Keep malformed or slash-suffixed ``/c`` paths out of the CMS fallback."""
    raise Http404('Content share link was not found.')
