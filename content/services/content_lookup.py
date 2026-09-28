"""Resolve synced content rows by their stable frontmatter UUID.

Shared by the public ``/c/<uuid>`` share-link redirect (issue #1802) and the
staff ``GET /api/content/<uuid>`` lookup endpoint (issue #1834), so both
surfaces agree on which models are addressable and on what "public" means.
"""

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

# (model, UUID field name, select_related fields, API type name)
SHAREABLE_MODELS = (
    (Article, 'content_id', (), 'article'),
    (Project, 'content_id', (), 'project'),
    (Tutorial, 'content_id', (), 'tutorial'),
    (Download, 'content_id', (), 'download'),
    (Workshop, 'content_id', (), 'workshop'),
    (WorkshopPage, 'content_id', ('workshop',), 'workshop_page'),
    (MarketingPage, 'content_id', (), 'marketing_page'),
    (Event, 'content_id', (), 'event'),
    (Course, 'source_content_id', (), 'course'),
    (Module, 'source_content_id', ('course', 'parent'), 'course_module'),
    (
        Unit,
        'source_content_id',
        ('module__course', 'module__parent'),
        'course_unit',
    ),
)

_TYPE_BY_MODEL = {model: type_name for model, _, _, type_name in SHAREABLE_MODELS}


def find_content_matches(content_id, limit=None):
    """Return rows whose stable UUID equals ``content_id``, in model order.

    ``limit`` stops scanning once that many rows were found (``/c/`` only
    needs two to detect ambiguity); ``None`` returns every match.
    """
    matches = []
    for model, field_name, related_fields, _type_name in SHAREABLE_MODELS:
        queryset = model.objects.filter(**{field_name: content_id}).order_by('pk')
        if related_fields:
            queryset = queryset.select_related(*related_fields)
        if limit is None:
            matches.extend(queryset)
        else:
            matches.extend(queryset[:limit - len(matches)])
            if len(matches) >= limit:
                break
    return matches


def content_type_name(target):
    """Return the API type name (``article``, ``course_unit``, ...) for a row."""
    return _TYPE_BY_MODEL.get(type(target), '')


def is_public_content(target):
    """True when ``/c/<uuid>`` would redirect anonymous visitors to ``target``."""
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
