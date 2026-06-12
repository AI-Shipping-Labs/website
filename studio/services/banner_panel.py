"""Shared context builder for the Studio banner/social-image panel (#931).

Every banner-bearing content edit view renders the same
``studio/includes/banner_generator_section.html`` include and therefore
needs the same set of context keys. This helper centralises that mapping so
the seven views (article, course, project, download, workshop, event,
event series) stay in lockstep — adding a panel control means editing one
function, not seven views.
"""

from django.urls import reverse

from integrations.services.banner_generator import (
    is_enabled as banner_generator_is_enabled,
)
from integrations.services.banner_generator.custom_upload import (
    is_upload_enabled,
)
from integrations.services.banner_generator.resolve import (
    banner_source,
    effective_banner_url,
)
from studio.services.banner_status import get_last_banner_task


def banner_panel_context(
    *,
    content_type,
    record,
    regenerate_url_name,
    upload_url_name,
    remove_url_name,
    url_kwarg,
):
    """Return the context dict the banner panel include needs for ``record``.

    Args:
        content_type: banner-pipeline slug (e.g. ``'article'``), used for the
            ``get_last_banner_task`` status lookup.
        record: the model instance being edited.
        regenerate_url_name / upload_url_name / remove_url_name: URL names of
            the three per-type POST endpoints.
        url_kwarg: the keyword-argument name those URLs expect (e.g.
            ``'article_id'``). All three share the same record pk kwarg.

    Returns the effective (precedence-resolved) banner URL + source badge,
    the raw ``custom_banner_url`` (drives the Remove control), the three POST
    URLs, and the two enabled gates (Lambda for Regenerate, CDN+bucket for
    Upload).
    """
    banner_enabled = banner_generator_is_enabled()
    kwargs = {url_kwarg: record.pk}
    return {
        'banner_url': effective_banner_url(record),
        'banner_source': banner_source(record),
        'custom_banner_url': getattr(record, 'custom_banner_url', '') or '',
        'banner_regenerate_url': reverse(regenerate_url_name, kwargs=kwargs),
        'banner_upload_url': reverse(upload_url_name, kwargs=kwargs),
        'banner_remove_url': reverse(remove_url_name, kwargs=kwargs),
        'banner_generator_enabled': banner_enabled,
        'banner_upload_enabled': is_upload_enabled(),
        'banner_last_task': (
            get_last_banner_task(content_type, record.pk)
            if banner_enabled else None
        ),
    }
