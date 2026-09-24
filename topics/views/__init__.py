"""Public views for member topic pages (#1688).

Since #1804 the surface is open to everyone: the hub and every published
topic page render their full body unconditionally, including for
anonymous visitors. Drafts 404 and the hub slug has no detail route.
"""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from topics.models import HUB_SLUG, STATUS_PUBLISHED, TopicPage


def _published_pages():
    """Every published topic page except the hub, in stable slug order."""
    return (
        TopicPage.objects.filter(status=STATUS_PUBLISHED)
        .exclude(slug=HUB_SLUG)
        .order_by('slug')
    )


@require_GET
def topics_hub(request):
    """The hub at /topics/: the index page body plus a discovery grid."""
    hub = TopicPage.objects.filter(
        slug=HUB_SLUG,
        status=STATUS_PUBLISHED,
    ).first()
    if hub is None:
        raise Http404('No published topic hub has been synced yet.')
    return render(
        request,
        'topics/hub.html',
        {
            'hub': hub,
            'pages': _published_pages(),
            'related_topics': hub.resolved_related(),
        },
    )


@require_GET
def topic_page(request, slug):
    """One topic page at /topics/<slug>/; drafts and the hub slug 404."""
    if slug == HUB_SLUG:
        raise Http404('The hub slug has no detail page.')
    page = TopicPage.objects.filter(
        slug=slug,
        status=STATUS_PUBLISHED,
    ).first()
    if page is None:
        raise Http404(f'No published topic page {slug!r}.')
    return render(
        request,
        'topics/detail.html',
        {
            'page': page,
            'related_topics': page.resolved_related(),
        },
    )
