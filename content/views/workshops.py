"""Public Workshop views (issue #296).

Renders the user-facing surface for the ``Workshop`` content type:

- ``/workshops`` — catalog of all published workshops.
- ``/workshops/<slug>`` — landing page (description + metadata) gated by
  ``landing_required_level``.
- ``/workshops/<slug>/video`` — recording panel + materials, gated by
  ``recording_required_level``.
- ``/workshops/<slug>/tutorial/<page_slug>`` — single tutorial page gated by
  ``pages_required_level`` with prev/next navigation.

Every section gates against its own field, so a Workshop with
``landing=0, pages=10, recording=20`` lets free visitors see the landing,
Basic+ members read the tutorial, and Main+ members watch the recording.

The catalog always shows every published workshop (with a tier badge) so
users see what they would unlock by upgrading.
"""

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from content.access import get_required_tier_name
from content.models import Workshop
from content.views.pages import _filter_by_tags, _get_selected_tags


def workshops_list(request):
    """Catalog page: grid of all published workshops."""
    workshops = Workshop.objects.filter(status='published').order_by('-date')
    selected_tags = _get_selected_tags(request)

    # Collect all tags from published workshops for the filter UI (mirrors
    # the courses_list pattern — chips are rendered inline on the cards).
    all_tags = set()
    for workshop in workshops:
        if workshop.tags:
            all_tags.update(workshop.tags)
    all_tags = sorted(all_tags)

    workshops = _filter_by_tags(workshops, selected_tags)

    context = {
        'workshops': workshops,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/workshops',
    }
    return render(request, 'content/workshops_list.html', context)


def _resolve_workshop(slug):
    """Fetch a published workshop or 404."""
    return get_object_or_404(Workshop, slug=slug, status='published')


def _build_landing_context(workshop, user):
    """Common context shared by the landing and other workshop pages.

    Returns the access flags, tier names, and CTA messages so each view
    can wire in the right paywall card without re-deriving the same state.
    """
    can_access_landing = workshop.user_can_access_landing(user)
    can_access_pages = workshop.user_can_access_pages(user)
    can_access_recording = workshop.user_can_access_recording(user)

    landing_tier_name = get_required_tier_name(
        workshop.landing_required_level,
    )
    pages_tier_name = get_required_tier_name(workshop.pages_required_level)
    recording_tier_name = get_required_tier_name(
        workshop.recording_required_level,
    )

    pages_cta_message = ''
    pages_cta_url = ''
    if not can_access_pages:
        pages_cta_message = (
            f'Upgrade to {pages_tier_name} to access this workshop'
        )
        pages_cta_url = '/pricing'

    recording_cta_message = ''
    recording_cta_url = ''
    if can_access_pages and not can_access_recording:
        recording_cta_message = (
            f'Upgrade to {recording_tier_name} to watch the recording'
        )
        recording_cta_url = '/pricing'

    landing_cta_message = ''
    landing_cta_url = ''
    if not can_access_landing:
        landing_cta_message = (
            f'Upgrade to {landing_tier_name} to view this workshop'
        )
        landing_cta_url = '/pricing'

    return {
        'workshop': workshop,
        'can_access_landing': can_access_landing,
        'can_access_pages': can_access_pages,
        'can_access_recording': can_access_recording,
        'landing_tier_name': landing_tier_name,
        'pages_tier_name': pages_tier_name,
        'recording_tier_name': recording_tier_name,
        'landing_cta_message': landing_cta_message,
        'landing_cta_url': landing_cta_url,
        'pages_cta_message': pages_cta_message,
        'pages_cta_url': pages_cta_url,
        'recording_cta_message': recording_cta_message,
        'recording_cta_url': recording_cta_url,
    }


def workshop_detail(request, slug):
    """Landing page: description, metadata, links to video and tutorial.

    The landing is always rendered for SEO — anonymous visitors see title
    and description even when ``landing_required_level > 0``, with the
    body replaced by an upgrade card.
    """
    workshop = _resolve_workshop(slug)
    user = request.user

    pages = list(workshop.pages.all().order_by('sort_order'))
    first_page = pages[0] if pages else None

    context = _build_landing_context(workshop, user)
    context.update({
        'pages': pages,
        'first_page': first_page,
        'event': workshop.event,
    })
    return render(request, 'content/workshop_detail.html', context)


def workshop_video(request, slug):
    """Video page: embedded recording + materials, gated by recording level.

    Lifted from the recording panel in ``templates/events/event_detail.html``
    so the video, timestamps, and materials render with the same player
    component used everywhere else on the site.
    """
    workshop = _resolve_workshop(slug)
    user = request.user

    context = _build_landing_context(workshop, user)
    context['event'] = workshop.event
    return render(request, 'content/workshop_video.html', context)


def workshop_page_detail(request, slug, page_slug):
    """Single tutorial page within a workshop, gated by pages level.

    Returns the page even when the user is below the gate so the page is
    SEO-indexable; the body is replaced by an upgrade card.
    """
    workshop = _resolve_workshop(slug)
    pages = list(workshop.pages.all().order_by('sort_order'))
    page = next((p for p in pages if p.slug == page_slug), None)
    if page is None:
        raise Http404('Workshop page not found')

    # Build prev/next using the in-memory list so we don't fire two extra
    # queries per request — the list is small (<=20 pages typically).
    idx = pages.index(page)
    prev_page = pages[idx - 1] if idx > 0 else None
    next_page = pages[idx + 1] if idx + 1 < len(pages) else None

    context = _build_landing_context(workshop, request.user)
    context.update({
        'page': page,
        'pages': pages,
        'prev_page': prev_page,
        'next_page': next_page,
        'is_gated': not context['can_access_pages'],
    })
    return render(request, 'content/workshop_page_detail.html', context)
