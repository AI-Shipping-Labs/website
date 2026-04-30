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

from django.http import Http404, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from content.access import get_required_tier_name, get_user_level
from content.models import Workshop, WorkshopPage
from content.services import completion as completion_service
from content.templatetags.video_utils import (
    append_query_param,
    detect_video_source,
    format_timestamp,
    parse_video_timestamp,
)
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
    current_user_state = ''
    if user.is_authenticated:
        current_user_state = (
            f'Current access: {get_required_tier_name(get_user_level(user))} member'
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
    if not can_access_recording:
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
        'current_user_state': current_user_state,
        'landing_cta_label': 'View Pricing',
        'pages_cta_label': 'View Pricing',
        'recording_cta_label': f'Upgrade to {recording_tier_name}',
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


def _build_timestamps_with_pages(event, workshop):
    """Annotate ``event.timestamps`` with the matching tutorial page (if any).

    Returns a list of dicts ``{time_seconds, formatted_time, label,
    tutorial_page}`` so the template can render the timestamp button
    plus an optional ``-> Tutorial: <title>`` sub-link without doing
    any time-parsing in Django template logic.

    Both timestamp shapes are accepted:
    - ``{time_seconds, label}`` (legacy / canonical)
    - ``{time, title}`` (workshop YAML)

    Pages are matched by exact-second equality. Duplicate ``video_start``
    values resolve to the page with the lowest ``sort_order`` (i.e. the
    first page reached in iteration order).
    """
    if not event:
        return []
    raw = event.timestamps or []

    # Build a {seconds: page} map from the workshop's pages. Iterate in
    # sort_order so setdefault keeps the lowest-ordered page on collision.
    page_by_seconds = {}
    for page in workshop.pages.all().order_by('sort_order'):
        if not page.video_start:
            continue
        try:
            seconds = parse_video_timestamp(page.video_start)
        except ValueError:
            continue
        page_by_seconds.setdefault(seconds, page)

    annotated = []
    for ts in raw:
        if not isinstance(ts, dict):
            continue
        # Resolve the integer seconds. Same logic as
        # video_utils.normalize_timestamps but also returns the matched
        # page so the template can render the sub-link.
        if 'time_seconds' in ts:
            try:
                seconds = int(ts.get('time_seconds') or 0)
            except (TypeError, ValueError):
                continue
        elif 'time' in ts:
            try:
                seconds = parse_video_timestamp(ts.get('time'))
            except ValueError:
                continue
        else:
            continue

        if seconds < 0:
            continue

        label = ts.get('label') or ts.get('title') or ''
        annotated.append({
            'time_seconds': seconds,
            'formatted_time': format_timestamp(seconds),
            'label': label,
            'tutorial_page': page_by_seconds.get(seconds),
        })
    return annotated


def workshop_video(request, slug):
    """Video page: embedded recording + materials, gated by recording level.

    Lifted from the recording panel in ``templates/events/event_detail.html``
    so the video, timestamps, and materials render with the same player
    component used everywhere else on the site.

    When loaded with a ``?t=MM:SS`` query string and the user has access
    to the recording, the embed is initialised at that offset. Each
    timestamp on the page that exact-matches a tutorial page's
    ``video_start`` shows a "-> Tutorial: <title>" sub-link.
    """
    workshop = _resolve_workshop(slug)
    user = request.user

    context = _build_landing_context(workshop, user)
    event = workshop.event
    context['event'] = event

    # Only parse ?t= and build the inverse-link map when the user can
    # actually watch the recording. Below the gate the player isn't
    # rendered, so any work here is wasted (and the spec calls this out
    # explicitly: "no ?t= parsing happens" when the paywall renders).
    embed_start_seconds = None
    timestamps_with_pages = []
    video_id = None
    video_source_type = None
    recording_embed_url_with_start = ''

    if context['can_access_recording'] and event:
        raw_t = request.GET.get('t', '')
        if raw_t:
            try:
                embed_start_seconds = parse_video_timestamp(raw_t)
            except ValueError:
                # Malformed ?t= silently ignored — the page still
                # renders 200 and the embed plays from 0.
                embed_start_seconds = None

        timestamps_with_pages = _build_timestamps_with_pages(event, workshop)

        if event.recording_url:
            video_source_type, video_id = detect_video_source(
                event.recording_url,
            )

        # Build the start-augmented embed URL for the legacy iframe path.
        if event.recording_embed_url:
            recording_embed_url_with_start = append_query_param(
                event.recording_embed_url,
                'start',
                embed_start_seconds,
            )

    context.update({
        'embed_start_seconds': embed_start_seconds,
        'recording_timestamps': timestamps_with_pages,
        'timestamps_with_pages': timestamps_with_pages,
        'video_id': video_id,
        'video_source_type': video_source_type,
        'recording_embed_url_with_start': recording_embed_url_with_start,
    })
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
    is_gated = not context['can_access_pages']

    # Show the "Watch this section" bar above the H1 only when:
    # - the page has a video_start timestamp, AND
    # - the user can access the recording, AND
    # - the page itself isn't gated (a user blocked from the body
    #   shouldn't see a watch link to the recording either, even if
    #   their tier somehow grants recording access).
    show_watch_bar = (
        bool(page.video_start)
        and context['can_access_recording']
        and not is_gated
    )
    if show_watch_bar:
        # Same-tab link (no target=_blank) so the user lands on the
        # video page in the normal navigation flow.
        watch_bar_url = (
            f'{workshop.get_absolute_url()}/video?t={page.video_start}'
        )
    else:
        watch_bar_url = ''

    # Issue #365 — server-rendered completion state powers both the
    # initial button styling and the "still completed after reload"
    # acceptance criterion. Anonymous users get False without a DB hit
    # (the service short-circuits) and the template hides the button
    # for them anyway.
    is_completed = (
        request.user.is_authenticated
        and not is_gated
        and completion_service.is_completed(request.user, page)
    )
    completed_page_ids = (
        completion_service.completed_ids_for(request.user, pages)
        if request.user.is_authenticated and not is_gated
        else set()
    )

    context.update({
        'page': page,
        'pages': pages,
        'prev_page': prev_page,
        'next_page': next_page,
        'is_gated': is_gated,
        'show_watch_bar': show_watch_bar,
        'watch_bar_url': watch_bar_url,
        'watch_bar_label': page.video_start,
        'is_completed': is_completed,
        'completed_page_ids': completed_page_ids,
        'user_authenticated': request.user.is_authenticated,
        'prev_item_url': prev_page.get_absolute_url() if prev_page else '',
        'prev_item_title': prev_page.title if prev_page else '',
        'next_item_url': next_page.get_absolute_url() if next_page else '',
        'next_item_title': next_page.title if next_page else '',
        'completion_kind': 'workshop',
        'completion_button_id': 'mark-page-complete-btn',
        'completion_button_testid': 'mark-page-complete-btn',
        'completion_url': (
            f'/api/workshops/{workshop.slug}/pages/{page.slug}/complete'
        ),
        'bottom_prev_testid': 'page-prev-btn',
        'bottom_next_testid': 'page-next-btn',
    })
    return render(request, 'content/workshop_page_detail.html', context)


def legacy_workshop_page_redirect(request, slug, page_slug):
    """Redirect old /workshops/<slug>/<page_slug> links to tutorial URLs."""
    if page_slug in {'tutorial', 'video'}:
        raise Http404('Workshop page not found')

    workshop = _resolve_workshop(slug)
    get_object_or_404(WorkshopPage, workshop=workshop, slug=page_slug)

    target_url = reverse(
        'workshop_page_detail',
        kwargs={'slug': slug, 'page_slug': page_slug},
    )
    query_string = request.META.get('QUERY_STRING')
    if query_string:
        target_url = f'{target_url}?{query_string}'

    return HttpResponsePermanentRedirect(target_url)


@require_POST
def api_workshop_page_complete(request, slug, page_slug):
    """POST /api/workshops/<slug>/pages/<page_slug>/complete — toggle.

    Mirrors :func:`content.views.courses.api_course_unit_complete` so
    the two content types share the same response contract:

    - 401 for anonymous callers.
    - 403 when the user is below ``pages_required_level``.
    - ``{"completed": true|false}`` on success.

    Toggle is delegated to the shared completion service so the write
    paths stay in one place.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    workshop = _resolve_workshop(slug)
    page = get_object_or_404(WorkshopPage, workshop=workshop, slug=page_slug)
    user = request.user

    if not workshop.user_can_access_pages(user):
        return JsonResponse({'error': 'Access denied'}, status=403)

    if completion_service.is_completed(user, page):
        completion_service.unmark_completed(user, page)
        return JsonResponse({'completed': False})

    completion_service.mark_completed(user, page)
    return JsonResponse({'completed': True})
