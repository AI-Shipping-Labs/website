"""Public views for member topic pages (#1688).

Both the hub and the topic pages are gated at the page's ``required_level``
(default Basic and above). Denied renders carry the canonical gated-access
card copy and never include the page's ``body`` or ``body_html``.
"""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from content.access import build_gating_context, can_access
from topics.models import HUB_SLUG, STATUS_PUBLISHED, TopicPage


def _gating_context(request, page):
    """Return the template context for one page's gate, or ``{'is_gated': False}``."""
    user = request.user
    if can_access(user, page):
        return {'is_gated': False}
    context = build_gating_context(
        user,
        page,
        content_type='topic',
        gated_card_testid='topics-gated-card',
        gated_cta_testid='topics-gated-cta',
        gated_icon='map',
    )
    if context.get('is_gated'):
        # TopicPage derives its teaser from the body; the shared builder
        # only knows the description/content_markdown attributes.
        context['teaser'] = page.teaser
        context['page_url'] = page.get_absolute_url()
    return context


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
    context = {
        'hub': hub,
        'pages': _published_pages(),
        'related_topics': hub.resolved_related(),
    }
    context.update(_gating_context(request, hub))
    return render(request, 'topics/hub.html', context)


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
    context = {
        'page': page,
        'related_topics': page.resolved_related(),
    }
    context.update(_gating_context(request, page))
    return render(request, 'topics/detail.html', context)
