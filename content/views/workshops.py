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

from urllib.parse import urlencode

from django.http import Http404, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from content.access import (
    LEVEL_BASIC,
    LEVEL_OPEN,
    LEVEL_REGISTERED,
    build_verify_email_context,
    get_required_tier_name,
    get_user_level,
)
from content.models import Workshop, WorkshopPage
from content.services import completion as completion_service
from content.templatetags.video_utils import (
    detect_video_source,
    format_timestamp,
    get_video_thumbnail_url,
    parse_video_timestamp,
)
from content.utils.teaser import truncate_to_words
from content.views.pages import _filter_by_tags, _get_selected_tags

# Approximate word budget for the locked-page teaser body. Mirrors the
# constant used by ``content.views.courses.TEASER_WORD_LIMIT`` so the
# same fade-out pattern shows on workshop tutorial / video pages.
TEASER_WORD_LIMIT = 150


def _gated_reason_for_level(user, required_level):
    """Return the gated reason for a workshop gate at ``required_level``.

    Mirrors :func:`content.access.get_gated_reason` but uses the bare
    level field instead of an instance attribute. Empty string means
    "user has access". The reasons match the values course units emit so
    template branches stay aligned.
    """
    # Anonymous on a registration wall: registered-required CTA.
    if (
        required_level == LEVEL_REGISTERED
        and (user is None or not user.is_authenticated)
    ):
        return 'authentication_required'
    # Free authenticated user blocked by email verification.
    if (
        required_level in (LEVEL_OPEN, LEVEL_REGISTERED)
        and user is not None
        and user.is_authenticated
        and not getattr(user, 'email_verified', False)
        and get_user_level(user) < LEVEL_BASIC
    ):
        return 'unverified_email'
    return 'insufficient_tier'


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

    Issue #571 fix: the pages paywall must branch on
    ``_gated_reason_for_level`` so an anonymous visitor on a workshop
    using the new ``pages_required_level=LEVEL_REGISTERED`` (5) default
    sees Sign-In-shaped copy (matching the tutorial-page paywall) rather
    than the nonsensical "Upgrade to Free" / "/pricing" combo.
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

    # Default the pages-paywall context to the empty / "no paywall" shape.
    # Each not-can_access branch fills these in; the insufficient-tier
    # path keeps the legacy "Upgrade to {tier}" copy and the
    # authentication-required path (anonymous + LEVEL_REGISTERED) emits
    # Sign-In-shaped copy to match _build_page_gated_context.
    pages_cta_message = ''
    pages_cta_url = ''
    pages_cta_label = 'View Pricing'
    pages_gated_description = (
        'The workshop overview and page list are visible now; '
        'membership unlocks the step-by-step tutorial.'
    )
    pages_required_tier_name = pages_tier_name
    pages_signup_cta_url = ''
    pages_signup_cta_label = ''
    if not can_access_pages:
        pages_gated_reason = _gated_reason_for_level(
            user, workshop.pages_required_level,
        )
        if pages_gated_reason == 'authentication_required':
            landing_url = workshop.get_absolute_url()
            next_qs = urlencode({'next': landing_url})
            pages_cta_message = 'Sign in to access this workshop'
            pages_gated_description = (
                'This workshop is free with a free account. Sign in or '
                'create one in seconds.'
            )
            pages_cta_url = f'/accounts/login/?{next_qs}'
            pages_cta_label = 'Sign In'
            pages_signup_cta_url = f'/accounts/signup/?{next_qs}'
            pages_signup_cta_label = 'Create a free account'
            # Blank the pill — there's no "tier" to display when the
            # visitor just needs to authenticate.
            pages_required_tier_name = ''
        else:
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
        'pages_cta_label': pages_cta_label,
        'pages_gated_description': pages_gated_description,
        'pages_required_tier_name': pages_required_tier_name,
        'pages_signup_cta_url': pages_signup_cta_url,
        'pages_signup_cta_label': pages_signup_cta_label,
        'recording_cta_message': recording_cta_message,
        'recording_cta_url': recording_cta_url,
        'current_user_state': current_user_state,
        'landing_cta_label': 'View Pricing',
        'recording_cta_label': f'Upgrade to {recording_tier_name}',
    }


def workshop_detail(request, slug):
    """Course-player layout (issue #618).

    Replaces the legacy three-route layout (landing card -> video page ->
    tutorial page) with a single unified player shell:

    - Left pane: chapter outline ("Recording outline"), tutorial pages
      TOC, and materials. Same component for locked and unlocked.
    - Right pane: the active tutorial page body, plus the per-section
      ``📽`` badges that anchor to the recording chapter timestamps.
    - Player iframe: rendered ONLY when the user can access the recording.
      Locked users get the same outline + tutorial pane but no player and
      no JS module — the only upsell surface is a discreet
      ``🔒 Recording · Get Premium`` link in the header strip.

    URL contract:
    - ``?page=<slug>`` selects the active tutorial (default = first).
    - ``?t=<seconds>`` is forwarded to the player as the start position.
    - ``?_partial=1`` returns only the tutorial-pane body so the JS
      module can swap the right pane without a full reload.
    """
    workshop = _resolve_workshop(slug)
    user = request.user
    event = workshop.event

    pages = list(workshop.pages.all().order_by('sort_order'))
    first_page = pages[0] if pages else None

    # Resolve the active page from ?page=<slug>; fall back to the first
    # tutorial. Unknown slugs silently fall back too — better UX than
    # 404'ing on a stale deep link.
    active_slug = (request.GET.get('page') or '').strip()
    active_page = None
    if active_slug:
        active_page = next(
            (p for p in pages if p.slug == active_slug), None,
        )
    if active_page is None:
        active_page = first_page

    context = _build_landing_context(workshop, user)
    can_access_recording = context['can_access_recording']

    # Player config: only built when we'll actually render the iframe.
    # For locked users every value below stays at its default (empty /
    # None) so the template branches cleanly omit the script tag and
    # the iframe markup.
    player_start_seconds = None
    video_id = None
    video_source_type = None
    embed_url = None
    timestamps_with_pages = []
    has_recording = bool(event and event.has_recording)

    if event and event.timestamps:
        timestamps_with_pages = _build_timestamps_with_pages(event, workshop)

    if can_access_recording and event and event.recording_url:
        raw_t = request.GET.get('t', '')
        if raw_t:
            try:
                player_start_seconds = parse_video_timestamp(raw_t)
            except ValueError:
                # Plain integer fallback ("?t=754") — the player_pane JS
                # accepts both shapes and we want both to work for deep
                # links from external sources.
                try:
                    player_start_seconds = int(raw_t)
                    if player_start_seconds < 0:
                        player_start_seconds = None
                except (TypeError, ValueError):
                    player_start_seconds = None

        video_source_type, video_id = detect_video_source(
            event.recording_url,
        )
        # Build a plain (non-API) embed URL so the template can lazy-load
        # the iframe via the JS module rather than mounting it on initial
        # paint. The JS module hydrates the YouTube IFrame API on first
        # interaction (saves a third-party request for read-only visits).
        if video_source_type == 'youtube' and video_id:
            embed_url = (
                f'https://www.youtube.com/embed/{video_id}?enablejsapi=1'
            )
            if player_start_seconds:
                embed_url = (
                    f'{embed_url}&start={player_start_seconds}'
                )
        elif video_source_type == 'loom' and video_id:
            embed_url = f'https://www.loom.com/embed/{video_id}'
            if player_start_seconds:
                embed_url = f'{embed_url}?t={player_start_seconds}'

    # Per-tutorial-page chapter badge map. For each page, find the
    # chapter timestamp(s) that fall in the page's video window
    # ``[page.video_start, next_page.video_start)``. The first matching
    # chapter becomes the page's primary ``📽 HH:MM:SS`` badge; the rest
    # render as inline badges next to a "In this section" footer.
    page_badges = _build_page_badges(pages, timestamps_with_pages)

    # Tutorial body for the right pane. ``_build_tutorial_pane_context``
    # mirrors the per-page state ``workshop_page_detail`` already builds
    # so the partial branches the same way for locked tutorials, the
    # bottom prev/next nav, and the per-page lock indicators.
    tutorial_pane_ctx = _build_tutorial_pane_context(
        request, workshop, pages, active_page,
    )

    # Active chapter selection. Used by the outline partial to render the
    # "now playing" highlight on the chapter row that lines up with the
    # active tutorial page (server-side default; the JS module updates
    # the highlight as the player advances).
    active_chapter_seconds = None
    if active_page and active_page.video_start:
        try:
            active_chapter_seconds = parse_video_timestamp(
                active_page.video_start,
            )
        except ValueError:
            active_chapter_seconds = None

    has_tutorials = bool(pages)

    context.update({
        'pages': pages,
        'first_page': first_page,
        'active_page': active_page,
        'event': event,
        'has_recording': has_recording,
        'has_tutorials': has_tutorials,
        # Player config (empty when locked or when no recording).
        'player_video_id': video_id,
        'player_source_type': video_source_type,
        'player_embed_url': embed_url,
        'player_start_seconds': player_start_seconds,
        'recording_timestamps': timestamps_with_pages,
        'timestamps_with_pages': timestamps_with_pages,
        'active_chapter_seconds': active_chapter_seconds,
        # Per-page badge map: {page_slug: {primary, extras}}.
        'page_badges': page_badges,
        'active_page_badges': page_badges.get(
            active_page.slug, {'primary': None, 'extras': []},
        ) if active_page else {'primary': None, 'extras': []},
        # Tutorial pane context (gated body, prev/next, etc.).
        **tutorial_pane_ctx,
    })

    # ?_partial=1: return the tutorial pane body block only so the JS
    # module can swap ``#workshop-tutorial-pane.innerHTML`` without a
    # full page reload. Used by chapter-click and tutorial-page-click
    # handlers in ``workshop_player.js``.
    if request.GET.get('_partial') == '1':
        return render(
            request, 'content/_workshop_tutorial_pane.html', context,
        )

    return render(request, 'content/workshop_detail.html', context)


def _build_page_badges(pages, timestamps_with_pages):
    """Compute the ``📽`` badge for each tutorial page.

    Each page's badge is the chapter timestamp that exact-matches the
    page's ``video_start`` (the canonical anchor). When multiple chapter
    timestamps fall inside the page's video window
    ``[page.video_start, next_page.video_start)``, the first match is the
    primary badge and the rest render as inline ``extras`` next to an
    "In this section" footer in the tutorial pane.
    """
    if not pages or not timestamps_with_pages:
        return {}

    # Pre-parse each page's start in seconds so we don't reparse on every
    # chapter row. Pages without a video_start get a ``None`` start — they
    # don't anchor a window and only their exact-matching chapter (if
    # any) becomes their primary badge.
    page_starts = []
    for p in pages:
        secs = None
        if p.video_start:
            try:
                secs = parse_video_timestamp(p.video_start)
            except ValueError:
                secs = None
        page_starts.append(secs)

    badges = {}
    for idx, page in enumerate(pages):
        start = page_starts[idx]
        end = None
        # Walk forward to find the next page that has a parsed start.
        # That forms the upper bound (exclusive) of this page's window.
        for nxt in page_starts[idx + 1:]:
            if nxt is not None:
                end = nxt
                break

        primary = None
        extras = []
        if start is None:
            # Without a window the page only owns chapters that explicitly
            # link to it via _build_timestamps_with_pages's exact-match.
            for ts in timestamps_with_pages:
                tp = ts.get('tutorial_page')
                if tp is not None and tp.pk == page.pk:
                    if primary is None:
                        primary = ts
                    else:
                        extras.append(ts)
        else:
            for ts in timestamps_with_pages:
                tsec = ts['time_seconds']
                if tsec < start:
                    continue
                if end is not None and tsec >= end:
                    continue
                if primary is None:
                    primary = ts
                else:
                    extras.append(ts)

        badges[page.slug] = {'primary': primary, 'extras': extras}
    return badges


def _build_tutorial_pane_context(request, workshop, pages, active_page):
    """Build the right-pane context for the player-shell layout.

    Mirrors the per-page state ``workshop_page_detail`` already produces
    (gating, prev/next, completion, watch-bar) so the new partial can be
    reused on both surfaces. ``active_page`` may be ``None`` when the
    workshop has zero tutorial pages — the partial then renders an empty
    state rather than a body.
    """
    if active_page is None:
        return {
            'page': None,
            'prev_page': None,
            'next_page': None,
            'is_gated': False,
            'is_completed': False,
            'completed_page_ids': set(),
            'show_watch_bar_player_shell': False,
            'page_video_start_seconds': None,
        }

    idx = pages.index(active_page)
    prev_page = pages[idx - 1] if idx > 0 else None
    next_page = pages[idx + 1] if idx + 1 < len(pages) else None

    page_can_access = workshop.user_can_access_pages(
        request.user, page=active_page,
    )
    is_gated = not page_can_access

    can_access_recording = workshop.user_can_access_recording(request.user)
    show_watch_bar_player_shell = (
        bool(active_page.video_start)
        and can_access_recording
        and not is_gated
    )
    page_video_start_seconds = None
    if active_page.video_start:
        try:
            page_video_start_seconds = parse_video_timestamp(
                active_page.video_start,
            )
        except ValueError:
            page_video_start_seconds = None

    is_completed = (
        request.user.is_authenticated
        and not is_gated
        and completion_service.is_completed(request.user, active_page)
    )
    completed_page_ids = (
        completion_service.completed_ids_for(request.user, pages)
        if request.user.is_authenticated and not is_gated
        else set()
    )

    return {
        'page': active_page,
        'prev_page': prev_page,
        'next_page': next_page,
        'is_gated': is_gated,
        'is_completed': is_completed,
        'completed_page_ids': completed_page_ids,
        'show_watch_bar_player_shell': show_watch_bar_player_shell,
        'page_video_start_seconds': page_video_start_seconds,
    }


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
    """Issue #618 — legacy ``/workshops/<slug>/video`` 301 redirects to
    the new course-player layout at ``/workshops/<slug>``.

    Preserves the ``?t=`` deep link so old chapter-link emails / shared
    URLs land on the right timestamp inside the new player. The legacy
    body below is dead code retained only to keep the imports stable;
    the function returns the redirect before reaching it.
    """
    # Resolve the workshop first so an unknown slug or a draft still
    # 404s — we never want a 301 chain that ends in a 404 (bad SEO and
    # bad UX for the visitor who sees a flash of redirect then nothing).
    workshop = _resolve_workshop(slug)
    target_url = workshop.get_absolute_url()
    raw_t = (request.GET.get('t') or '').strip()
    if raw_t:
        target_url = f'{target_url}?{urlencode({"t": raw_t})}'
    return HttpResponsePermanentRedirect(target_url)


def workshop_page_detail(request, slug, page_slug):
    """Single tutorial page within a workshop, gated by pages level.

    Returns the page even when the user is below the gate so the page is
    SEO-indexable; the body is replaced by a teaser-with-fade preview
    plus an upgrade or sign-in card. Mirrors the course-unit teaser
    layout from issue #248 so the gating UX stays consistent across
    content types.
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
    # Issue #571: per-page override beats the workshop-wide pages gate.
    # Compute page-level access and drive ``is_gated`` and the watch-bar
    # branch off it so an ``access: open`` page lets anonymous visitors
    # read the body even when ``pages_required_level`` is registered or
    # basic+. ``can_access_pages`` in the shared context still reflects
    # the workshop-wide gate (consumed by templates that care about the
    # workshop as a whole, not this specific page).
    page_can_access = workshop.user_can_access_pages(request.user, page=page)
    context['can_access_pages'] = page_can_access
    is_gated = not page_can_access

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
        # Issue #618: the standalone tutorial page links into the new
        # course-player layout (the `/video` route is retired and 301s
        # back to the player anyway). The ``?page=`` query selects the
        # active tutorial in the player so the right pane lands on this
        # exact tutorial; ``?t=`` seeks the player.
        watch_bar_url = (
            f'{workshop.get_absolute_url()}'
            f'?page={page.slug}&t={page.video_start}'
        )
    else:
        watch_bar_url = ''

    # Issue #618: "View in player" bridge link from the standalone
    # tutorial route to the new course-player layout. Always rendered
    # (even when the user lacks recording access — the player layout
    # then shows the same tutorial body without a player iframe).
    view_in_player_url = (
        f'{workshop.get_absolute_url()}?page={page.slug}'
    )

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
        'view_in_player_url': view_in_player_url,
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
        # Issue #517 — mobile progress bar context. The bar is rendered
        # only on the non-gated branch; we still provide values on the
        # gated branch so any test that inspects the context after a
        # gated render gets stable defaults instead of KeyError.
        'reader_mobile_label': 'Workshop Navigation',
        'reader_progress_kind': 'page',
        'reader_progress_current': idx + 1,
        'reader_progress_total': len(pages),
        'reader_progress_completed': len(completed_page_ids),
    })

    status = 200
    if is_gated:
        gated_extras, gated_status = _build_page_gated_context(
            request, workshop, page,
        )
        context.update(gated_extras)
        status = gated_status

    return render(
        request, 'content/workshop_page_detail.html', context, status=status,
    )


def _build_page_gated_context(request, workshop, page):
    """Build the teaser / sign-in / verify-email context for a gated page.

    Mirrors the course-unit teaser pattern (issue #248): the template
    expects ``teaser_body_html``, ``video_thumbnail_url``, ``has_video``,
    ``signup_cta_url``, ``signup_cta_label``, ``gated_heading``,
    ``gated_description``, ``gated_cta_url``, ``gated_cta_label``,
    ``gated_card_testid``, ``gated_cta_testid``, ``gated_reason``.

    Returns ``(context_extras, http_status)`` where the status is 200 for
    the unverified-email branch (the user can resolve it without leaving
    the page) and 403 otherwise.
    """
    user = request.user
    page_url = page.get_absolute_url()
    # Issue #571: use the page's effective level (override beats workshop
    # default) so a registered-wall page on a Basic-default workshop still
    # surfaces the "sign in" CTA, not the upgrade CTA.
    effective_level = page.effective_required_level
    gated_reason = _gated_reason_for_level(user, effective_level)

    if gated_reason == 'unverified_email':
        return (
            {
                'gated_reason': 'unverified_email',
                **build_verify_email_context(user),
            },
            200,
        )

    # Build teaser body (HTML-aware truncation). Empty body falls back
    # to the bare paywall card so we never render an empty fade-out.
    teaser_body_html = None
    if page.body_html:
        teaser_body_html = truncate_to_words(page.body_html, TEASER_WORD_LIMIT)

    # Locked-video thumbnail (YouTube hqdefault) when the page anchors a
    # section of the workshop recording. Loom is supported too via
    # ``get_video_thumbnail_url``.
    has_video = False
    video_thumbnail_url = None
    event = workshop.event
    if page.video_start and event and event.recording_url:
        thumb = get_video_thumbnail_url(event.recording_url)
        if thumb:
            has_video = True
            video_thumbnail_url = thumb

    pages_tier_name = get_required_tier_name(effective_level)
    next_qs = urlencode({'next': page_url})

    if gated_reason == 'authentication_required':
        gated_heading = 'Sign in to keep reading this tutorial'
        gated_description = (
            'This tutorial is free with a free account. Sign in or create '
            'one in seconds.'
        )
        gated_cta_url = f'/accounts/login/?{next_qs}'
        gated_cta_label = 'Sign In'
        signup_cta_url = f'/accounts/signup/?{next_qs}'
        signup_cta_label = 'Create a free account'
        required_tier_name = ''
        current_user_state = ''
    else:
        # Insufficient-tier path: anonymous on a paid workshop or signed-in
        # user below ``pages_required_level``.
        gated_heading = (
            f'Upgrade to {pages_tier_name} to access this workshop'
        )
        gated_description = (
            'The page title and workshop navigation are visible now; '
            'membership unlocks the tutorial body.'
        )
        gated_cta_url = '/pricing'
        gated_cta_label = 'View Pricing'
        required_tier_name = pages_tier_name
        current_user_state = ''
        if user.is_authenticated:
            current_user_state = (
                f'Current access: {get_required_tier_name(get_user_level(user))} member'
            )
        signup_cta_url = ''
        signup_cta_label = ''
        if not user.is_authenticated:
            # Anonymous on a paid-tier wall: surface a "create a free
            # account" companion to the upgrade button so the visitor
            # has a no-cost path to start the funnel.
            signup_cta_url = f'/accounts/signup/?{next_qs}'
            signup_cta_label = 'Create a free account'

    return (
        {
            'gated_reason': gated_reason,
            'teaser_body_html': teaser_body_html,
            'video_thumbnail_url': video_thumbnail_url,
            'has_video': has_video,
            'signup_cta_url': signup_cta_url,
            'signup_cta_label': signup_cta_label,
            'gated_card_testid': 'page-paywall',
            'gated_icon': 'book-open',
            'gated_heading': gated_heading,
            'gated_description': gated_description,
            'required_tier_name': required_tier_name,
            'current_user_state': current_user_state,
            'gated_cta_url': gated_cta_url,
            'gated_cta_label': gated_cta_label,
            'gated_cta_testid': 'page-upgrade-cta',
        },
        403,
    )


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

    # Issue #571: gate against the page's effective level (per-page override
    # wins over the workshop default) so a free user can mark an
    # ``access: open`` page complete even when the workshop-wide gate is
    # higher.
    if not workshop.user_can_access_pages(user, page=page):
        return JsonResponse({'error': 'Access denied'}, status=403)

    if completion_service.is_completed(user, page):
        completion_service.unmark_completed(user, page)
        return JsonResponse({'completed': False})

    completion_service.mark_completed(user, page)
    return JsonResponse({'completed': True})
