import calendar as cal_module
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.services.timezones import format_user_datetime
from content.access import (
    build_gating_context,
    can_access,
    get_required_tier_name,
)
from events.models import (
    Event,
    EventFeedback,
    EventJoinClick,
    EventRegistration,
    EventSeries,
)
from events.services.calendar_feed import (
    build_subscribe_urls,
    feed_events_queryset,
)
from events.services.calendar_invite import generate_feed_ics, generate_ics
from events.services.cancel_token import (
    CancelTokenExpired,
    CancelTokenInvalid,
    decode_cancel_token,
)
from events.services.display_time import (
    build_event_time_display,
    should_display_event_location,
)

VALID_EVENTS_FILTERS = {'all', 'upcoming', 'past'}


def _get_selected_tags(request):
    """Extract selected tags from query params. Supports ?tag=X&tag=Y."""
    return [t.strip() for t in request.GET.getlist('tag') if t.strip()]


def _filter_by_tags(queryset, selected_tags):
    """Filter a queryset by multiple tags with AND logic.

    Returns a filtered queryset containing only items that have ALL selected tags.
    """
    if not selected_tags:
        return queryset
    matching_ids = []
    for obj in queryset:
        obj_tags = set(obj.tags or [])
        if all(tag in obj_tags for tag in selected_tags):
            matching_ids.append(obj.pk)
    return queryset.filter(pk__in=matching_ids)


def events_calendar(request, year=None, month=None):
    """Monthly calendar grid view for events."""
    today = date.today()
    year = year or today.year
    month = month or today.month

    # Clamp month to valid range
    if month < 1 or month > 12:
        from django.http import Http404
        raise Http404

    # Build calendar grid (Monday start)
    cal = cal_module.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)

    # Get events for this month (non-draft)
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, month + 1, 1)

    events = Event.objects.filter(
        start_datetime__date__gte=month_start,
        start_datetime__date__lt=month_end,
    ).exclude(status='draft').order_by('start_datetime')

    # Map events to days
    events_by_day = {}
    for event in events:
        day = event.start_datetime.date().day
        events_by_day.setdefault(day, []).append(event)

    # Build grid with events
    weeks = []
    for week in month_days:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append({'day': 0, 'events': [], 'is_today': False})
            else:
                week_data.append({
                    'day': day,
                    'events': events_by_day.get(day, []),
                    'is_today': (
                        day == today.day
                        and month == today.month
                        and year == today.year
                    ),
                })
        weeks.append(week_data)

    # Navigation
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    month_name = cal_module.month_name[month]

    # Build agenda list for mobile: only days with events, sorted
    agenda_days = []
    for day_num in sorted(events_by_day.keys()):
        agenda_days.append({
            'day': day_num,
            'date': date(year, month, day_num),
            'events': events_by_day[day_num],
        })

    context = {
        'weeks': weeks,
        'month_name': month_name,
        'year': year,
        'month': month,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'today': today,
        'events_list': events,
        'agenda_days': agenda_days,
        'subscribe_urls': build_subscribe_urls(),
    }
    return render(request, 'events/events_calendar.html', context)


def events_list(request):
    """Events list page with Upcoming and Past sections.

    Accepts ``?filter=`` with values ``all`` (default), ``upcoming``, or
    ``past``. The past surface filters to completed events that have a
    recording URL, supports tag filtering via ``?tag=``, and paginates at
    20 per page.
    """
    # Exclude draft events from public listing
    events = Event.objects.exclude(status='draft')

    filter_mode = request.GET.get('filter', 'all').strip().lower()
    if filter_mode not in VALID_EVENTS_FILTERS:
        filter_mode = 'all'

    selected_tags = _get_selected_tags(request)

    # Issue #713: derive upcoming / past from timestamps so a stale
    # ``status='upcoming'`` row whose effective end has passed lands
    # in the past bucket immediately. ``effective end`` is
    # ``end_datetime`` when set, else ``start_datetime + 1h``.
    now = timezone.now()
    one_hour = timedelta(hours=1)
    upcoming_filter = (
        Q(end_datetime__gt=now)
        | Q(end_datetime__isnull=True, start_datetime__gt=now - one_hour)
    )
    past_filter = (
        Q(end_datetime__lte=now)
        | Q(end_datetime__isnull=True, start_datetime__lte=now - one_hour)
    )

    upcoming_events = events.filter(upcoming_filter).exclude(
        status='cancelled',
    ).order_by('start_datetime')

    # For the "past" surface we show finished events with a recording
    # (and honor the ``published`` flag). The default "all" view also
    # includes cancelled events and does not require a recording.
    past_with_recording_qs = events.filter(past_filter).filter(
        published=True,
    ).exclude(
        status='cancelled',
    ).exclude(
        recording_url='',
    ).exclude(
        recording_url__isnull=True,
    ).order_by('-start_datetime')

    # ``past_all_qs`` = any event past its effective end, OR any
    # cancelled event (which is treated as past regardless of time).
    past_all_qs = events.filter(
        past_filter | Q(status='cancelled'),
    ).order_by('-start_datetime')

    # Collect all tags from past-with-recording events for the tag filter UI
    all_past_tags = set()
    for event in past_with_recording_qs:
        if event.tags:
            all_past_tags.update(event.tags)
    all_past_tags = sorted(all_past_tags)

    # Apply tag filtering only on past-with-recording list.
    past_filtered = _filter_by_tags(past_with_recording_qs, selected_tags)

    # Paginate the past-with-recording list (20 per page) when filter=past.
    page_obj = None
    is_paginated = False
    if filter_mode == 'past':
        paginator = Paginator(past_filtered, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        is_paginated = page_obj.has_other_pages()
        past_events = page_obj
    elif filter_mode == 'all':
        past_events = past_all_qs
    else:
        # upcoming: we don't render past section
        past_events = past_all_qs.none()

    # Annotate events with registration info for authenticated users
    user = request.user
    registered_event_ids = set()
    if user.is_authenticated:
        registered_event_ids = set(
            EventRegistration.objects.filter(
                user=user,
            ).values_list('event_id', flat=True)
        )

    # Issue #578: "Subscribe to all events" CTA on the view-toggle row.
    # Three options resolved server-side so the template stays free of
    # URL-encoding logic: Google deep-link, Apple webcal://, and the
    # canonical https:// URL exposed for copy-paste into Outlook etc.
    subscribe_urls = build_subscribe_urls()

    context = {
        'filter_mode': filter_mode,
        'show_upcoming': filter_mode in ('all', 'upcoming'),
        'show_past': filter_mode in ('all', 'past'),
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'page_obj': page_obj,
        'is_paginated': is_paginated,
        'all_past_tags': all_past_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'registered_event_ids': registered_event_ids,
        'base_path': '/events',
        'subscribe_urls': subscribe_urls,
    }
    return render(request, 'events/events_list.html', context)


@login_required
def event_join_redirect(request, slug):
    """Redirect registered users to the event join URL, tracking each click."""
    event = get_object_or_404(Event, slug=slug)

    # Draft events return 404 for non-staff users
    if event.status == 'draft' and not request.user.is_staff:
        raise Http404

    # User must be registered for the event
    is_registered = EventRegistration.objects.filter(
        event=event, user=request.user,
    ).exists()
    if not is_registered:
        # Issue #673: ``event_detail`` is keyed on ``event_id`` + ``slug``
        # now. ``Event.get_absolute_url`` is the single source of truth
        # for the canonical URL shape.
        return redirect(event.get_absolute_url())

    # Past events show an unavailable page. The "Back to event" link there
    # already surfaces the inline recording on /events/<slug>.
    # Issue #713: gate on the time-derived ``is_past`` so a legacy
    # ``status='upcoming'`` row whose ``end_datetime`` has passed is also
    # treated as past without waiting for the daily cron.
    if event.is_past:
        return render(request, 'events/join_unavailable.html', {
            'event': event,
            'reason': 'past',
        })

    # No join URL yet
    if not event.zoom_join_url:
        return render(request, 'events/join_unavailable.html', {
            'event': event,
            'reason': 'no_url',
        })

    # Issue #704: time-gate the redirect to Zoom so a participant landing
    # on the platform Join link well before ``start_datetime`` does not
    # auto-start the Zoom cloud recording. Branches:
    #   - delta > 10 min -> too-early page (HTTP 200)
    #   - 5 min < delta <= 10 min -> live countdown page (HTTP 200)
    #   - delta <= 5 min AND now is still inside the live window -> 302
    #   - now is past the live-window cutoff -> 'past' unavailable page
    # Issue #712: the live window is ``[start, Event.effective_end_datetime]``
    # — ``end_datetime`` when set, otherwise ``start + 1h``. The 1h
    # fallback is shared with ``complete_finished_events``, the .ics
    # export, the calendar deep-link builders, and Studio's default
    # duration so every surface agrees on "this event is over".
    now = timezone.now()
    delta = event.start_datetime - now

    if now > event.effective_end_datetime:
        # Past the live window — treat as a past event even if the cron
        # has not yet flipped ``status`` to ``completed``. This branch
        # is intentionally evaluated against the timestamp only, not
        # ``event.status``, so a stale ``status='upcoming'`` row past
        # its end still blocks here.
        return render(request, 'events/join_unavailable.html', {
            'event': event,
            'reason': 'past',
        })

    if delta > timedelta(minutes=10):
        return render(request, 'events/join_too_early.html', {
            'event': event,
            'event_start_local': format_user_datetime(
                event.start_datetime, request.user,
            ),
        })

    if delta > timedelta(minutes=5):
        # Server-rendered initial seconds counts down to ``start - 5 min``.
        # The inline JS ticks the visible timer every 1s; the meta refresh
        # re-evaluates this branch every 30s so the next request 302s
        # once the join window opens.
        seconds_until_open = max(
            int((delta - timedelta(minutes=5)).total_seconds()),
            0,
        )
        return render(request, 'events/join_countdown.html', {
            'event': event,
            'event_start_local': format_user_datetime(
                event.start_datetime, request.user,
            ),
            'seconds_until_open': seconds_until_open,
            'minutes_until_open': seconds_until_open // 60,
            'remaining_seconds': seconds_until_open % 60,
        })

    # delta <= 5 min AND now <= end_or_grace_cutoff: record click and
    # redirect to Zoom.
    EventJoinClick.objects.create(event=event, user=request.user)
    return redirect(event.zoom_join_url)


@ensure_csrf_cookie
def event_detail(request, event_id, slug):
    """Event detail page - always visible to everyone.

    Issue #513: this view sets the ``csrftoken`` cookie for fresh
    anonymous visitors so the email-only registration form rendered in
    ``templates/events/event_detail.html`` can read it via
    ``getCookie('csrftoken')`` and POST successfully. Without this
    decorator the first POST from an anonymous session would return 403.

    Issue #673: lookup is by integer ``event_id`` only — the ``slug``
    segment is cosmetic. When the URL slug does not match the stored
    slug we 301 to the canonical form so external links survive
    rename-by-slug. The 301 (not 302) lets search engines collapse the
    two URLs into one.
    """
    event = get_object_or_404(Event, pk=event_id)

    # Issue #673: redirect to canonical when the cosmetic slug doesn't
    # match the stored slug. The check runs BEFORE the draft gate so a
    # stale share-on-X link with the old slug still redirects rather
    # than 404s; the draft visibility check then applies on the
    # canonical URL.
    if slug != event.slug:
        return redirect(event.get_absolute_url(), permanent=True)

    # Draft events should not be publicly visible
    if event.status == 'draft' and not request.user.is_staff:
        from django.http import Http404
        raise Http404

    user = request.user

    # Issue #572: external events bypass the in-app registration flow
    # entirely. The detail page renders an outbound Join card instead
    # of the registration card, ignoring ``required_level`` for access
    # control (the third-party platform handles access on their side).
    is_external = event.is_external

    # Check access for registration gating
    has_access = can_access(user, event)

    # Check if user is registered
    is_registered = False
    if user.is_authenticated:
        is_registered = EventRegistration.objects.filter(
            event=event, user=user,
        ).exists()

    # Build gating context for the upcoming-event registration CTA. The
    # event detail page is announcement-only (issue #426) — recording
    # playback and its paywall live on the linked Workshop, so we always
    # use the 'event' gating copy here.
    gating = build_gating_context(user, event, 'event')

    # Determine if we should show the join link.
    # Issue #713: gate on time-derived ``is_upcoming`` so a stale
    # ``status='upcoming'`` row whose end has passed no longer offers
    # the join link.
    show_join_link = (
        is_registered
        and event.can_show_zoom_link()
        and event.is_upcoming
    )

    # Determine required tier name for CTA
    required_tier_name = get_required_tier_name(event.required_level)

    # Issue #513: anonymous email-only registration flow. After a
    # successful POST the JS reloads the page with ``?registered=<email>``
    # so the template can render a confirmation block instead of the
    # signup form. We do NOT trust this query param to mean a row was
    # actually created — only that the JS thinks the registration
    # succeeded. The block is confirmation copy, not access control.
    #
    # Issue #572: external events never offer in-app registration so the
    # ``?registered=<email>`` confirmation block is suppressed for them
    # — there's nothing to confirm.
    anon_registered_email = ''
    anon_registered_account_created = False
    if (
        not user.is_authenticated
        and event.is_upcoming
        and not is_external
    ):
        raw_email = (request.GET.get('registered') or '').strip()
        # Only render the confirmation when the email looks like an
        # email; ignores junk like ``?registered=1``.
        if raw_email and '@' in raw_email and '.' in raw_email.split('@', 1)[-1]:
            anon_registered_email = raw_email
            anon_registered_account_created = (
                request.GET.get('account_created') == '1'
            )

    # Issue #679: feedback surface — only meaningful once the event has
    # ended. The public aggregate counts only rated entries (rating IS
    # NOT NULL); comment-only rows don't move the rating average.
    # Issue #713: gate on the model's time-derived ``is_past`` so the
    # feedback form opens automatically once the effective end passes,
    # without a cron run.
    event_is_past = event.is_past
    feedback_qs = event.feedback.all()
    feedback_aggregate = feedback_qs.aggregate(avg=Avg('rating'))
    feedback_avg = feedback_aggregate['avg']
    if feedback_avg is not None:
        feedback_avg = round(feedback_avg, 1)
    feedback_count = feedback_qs.filter(rating__isnull=False).count()
    user_feedback = None
    if user.is_authenticated:
        user_feedback = feedback_qs.filter(user=user).first()
    can_submit_feedback = is_registered and event_is_past
    feedback_thanks = request.GET.get('feedback') == 'thanks'

    context = {
        'event': event,
        'event_time_display': build_event_time_display(event, user),
        'has_access': has_access,
        'is_registered': is_registered,
        'show_event_location': should_display_event_location(event),
        'show_zoom_link': show_join_link,
        'required_tier_name': required_tier_name,
        # Issue #484: ordered list of speakers/instructors for the
        # detail-page header. Pre-computed so the template can branch on
        # presence without re-querying the through-model.
        'event_instructors': event.ordered_instructors,
        # Issue #484: surface a download URL for the .ics file so
        # registered users can re-add the event to their calendar
        # independent of email delivery. Issue #673: the .ics download
        # route still keys by slug (slug-keyed sibling routes were
        # intentionally left unchanged), so build the URL from
        # ``event.slug`` here rather than the new id+slug helper.
        'event_ics_url': f'/events/{event.slug}/calendar.ics',
        # Issue #513: post-registration confirmation copy for the
        # anonymous email-only flow.
        'anon_registered_email': anon_registered_email,
        'anon_registered_account_created': anon_registered_account_created,
        # Issue #572: pre-computed branch flag the template uses to swap
        # the registration card for the external Join card.
        'is_external_event': is_external,
        # Issue #679: post-event feedback surface. ``event_is_past`` is
        # the template-side gate for both the aggregate badge and the
        # form. ``user_feedback`` pre-populates the form on subsequent
        # visits; the submit-button label flips to "Update feedback"
        # when this is non-null.
        'event_is_past': event_is_past,
        'feedback_avg': feedback_avg,
        'feedback_count': feedback_count,
        'user_feedback': user_feedback,
        'can_submit_feedback': can_submit_feedback,
        'feedback_thanks': feedback_thanks,
    }
    context.update(gating)
    return render(request, 'events/event_detail.html', context)


@login_required
@require_POST
def event_feedback_submit(request, event_id, slug):
    """Accept a post-event feedback submission from a registered attendee.

    Issue #679. Gating (all rejections return ``HttpResponseForbidden``
    with a clear message; no row is created):

    - ``@login_required``: anonymous → redirect to login.
    - Registered for the event: a non-attendee gets 403.
    - ``event.end_datetime <= now``: submitting before the event ends
      gets 403 ("Feedback opens after the event ends").

    On valid POST, ``update_or_create`` overwrites any existing row for
    this (event, user) pair and redirects back to
    ``event.get_absolute_url() + '?feedback=thanks'`` so the detail
    template can render the confirmation block.

    The URL is registered BEFORE the canonical
    ``events/<int:event_id>/<slug:slug>`` route so the literal
    ``feedback`` segment is not swallowed (same pattern as
    ``events/<slug>/join``).
    """
    event = get_object_or_404(Event, pk=event_id)

    # Slug mismatch redirects to the canonical URL — same pattern as
    # event_detail. We redirect to the canonical feedback URL so the
    # subsequent POST goes through cleanly; in practice templates and
    # external callers should always mint the canonical form.
    if slug != event.slug:
        return redirect(
            f'/events/{event.pk}/{event.slug}/feedback',
            permanent=True,
        )

    is_registered = EventRegistration.objects.filter(
        event=event, user=request.user,
    ).exists()
    if not is_registered:
        return HttpResponseForbidden(
            'Only registered attendees can leave feedback.'
        )

    if event.end_datetime is None or event.end_datetime > timezone.now():
        return HttpResponseForbidden(
            'Feedback opens after the event ends.'
        )

    rating_raw = (request.POST.get('rating') or '').strip()
    comment = (request.POST.get('comment') or '').strip()
    would_change = (request.POST.get('would_change') or '').strip()

    rating = None
    if rating_raw:
        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            return HttpResponseForbidden('Rating must be a number 1-5.')
        if rating < 1 or rating > 5:
            return HttpResponseForbidden('Rating must be between 1 and 5.')

    if rating is None and not comment and not would_change:
        return HttpResponseForbidden(
            'Please leave a rating or a comment.'
        )

    EventFeedback.objects.update_or_create(
        event=event,
        user=request.user,
        defaults={
            'rating': rating,
            'comment': comment,
            'would_change': would_change,
        },
    )
    return redirect(f'{event.get_absolute_url()}?feedback=thanks')


def event_detail_no_slug_redirect(request, event_id):
    """Permanent redirect from ``/events/<id>`` to the canonical id+slug URL.

    Issue #673: a share-on-X link without the cosmetic slug segment
    (``/events/42`` or ``/events/42/``) still resolves to the canonical
    ``/events/42/<slug>`` form. The redirect is a 301 so search engines
    collapse the two URLs into one and crawlers don't re-fetch the bare
    id route.

    A draft event is treated the same as any other event here — the
    redirect itself does not gate on visibility, the canonical detail
    view does. This matches the slug-mismatch redirect inside
    ``event_detail`` so the two id-routes have a consistent shape.
    """
    event = get_object_or_404(Event, pk=event_id)
    return redirect(event.get_absolute_url(), permanent=True)


@ensure_csrf_cookie
def event_series_public(request, slug):
    """Public series index page.

    Issue #564 (renamed from ``event_group_public`` in #575). Shows the
    series' metadata and every published member event. Anonymous visitors
    see the page; per-event tier gating happens on the individual event
    detail / registration as today.

    Issue #857: sets the ``csrftoken`` cookie so the inline series-register
    fetch can POST with a valid token, and surfaces per-occurrence
    registration state plus the standing series-registration flag.

    Draft events are hidden from anonymous and non-staff visitors. Staff
    see every member event so the page is useful for previewing a
    series before publishing.
    """
    series = get_object_or_404(EventSeries, slug=slug)
    # Issue #668: annotate Count('registrations') so the attendee-count
    # chip on every card resolves from the SELECT, not from N follow-up
    # `COUNT(*)` queries. The template reads `event.attendee_count`,
    # which prefers the annotation when set.
    events = list(
        series.events.annotate(
            _attendee_count=Count('registrations'),
        ).order_by('series_position', 'start_datetime')
    )
    if not request.user.is_staff:
        events = [e for e in events if e.status != 'draft']

    user = request.user

    # Issue #857: per-occurrence registration state and the standing
    # series-registration flag drive the register UI on this page.
    is_series_registered = False
    registered_event_ids = set()
    if user.is_authenticated:
        from events.models import SeriesRegistration
        is_series_registered = SeriesRegistration.objects.filter(
            series=series, user=user,
        ).exists()
        registered_event_ids = set(
            EventRegistration.objects.filter(
                user=user, event__in=events,
            ).values_list('event_id', flat=True)
        )

    # Annotate each occurrence with the state the template renders:
    # ``registered`` / ``register`` / ``full`` / ``past`` / ``no_access``.
    for event in events:
        if event.is_past:
            event.user_reg_state = 'past'
        elif user.is_authenticated and event.id in registered_event_ids:
            event.user_reg_state = 'registered'
        elif user.is_authenticated and not can_access(user, event):
            event.user_reg_state = 'no_access'
        elif event.is_full:
            event.user_reg_state = 'full'
        else:
            event.user_reg_state = 'register'

    # ``upcoming_registrable`` is what the "Register for all upcoming
    # sessions" button actually enrolls into — future, non-cancelled,
    # non-draft, accessible, not-already-registered occurrences. We use it
    # to decide whether the primary button is meaningful at all.
    has_upcoming_to_register = any(
        e.user_reg_state == 'register' for e in events
    )

    # Issue #857: surface partial-tier context on GET by counting
    # tier-locked upcoming occurrences for authenticated users so the
    # page can show the upgrade nudge alongside the register button.
    upcoming_count = sum(
        1 for e in events
        if not e.is_past and e.status not in ('draft', 'cancelled')
    )
    tier_locked_count = sum(
        1 for e in events if e.user_reg_state == 'no_access'
    )
    has_tier_locked = tier_locked_count > 0

    return render(request, 'events/event_series.html', {
        'series': series,
        'events': events,
        'is_series_registered': is_series_registered,
        'has_upcoming_to_register': has_upcoming_to_register,
        'upcoming_count': upcoming_count,
        'tier_locked_count': tier_locked_count,
        'has_tier_locked': has_tier_locked,
        'series_register_url': f'/api/events/series/{series.slug}/register',
        # Login redirect target for anonymous visitors clicking register.
        'login_next': series.get_absolute_url(),
        'pricing_url': '/pricing',
    })


def _resolve_cancel_state(slug, token):
    """Decode the token and load the registration row.

    Returns a tuple ``(state, context)`` where ``state`` is one of
    ``"confirm"``, ``"invalid"``, ``"expired"``, ``"already_cancelled"``,
    or ``"event_finished"`` and ``context`` is a dict of template
    variables. The view layer wraps the result in either the confirm or
    the result template.

    The same logic backs both the GET confirmation page and the POST
    action so the user sees consistent messaging across the two-step
    flow.

    Issue #673: the ``slug`` argument is the URL path segment, which
    still keys the slug-only cancel-registration sibling route. The
    cosmetic ``event_url`` we render in error states points to the
    canonical id+slug URL when we can resolve the event, and falls back
    to the events list when we can't (no id available).
    """
    # Fallback used by error branches that don't have an Event in
    # scope. ``/events`` is the public list and is always safe to
    # offer.
    event_url = '/events'

    if not token:
        return 'invalid', {
            'message': 'This cancellation link is incomplete.',
            'event_url': event_url,
        }

    try:
        payload = decode_cancel_token(token)
    except CancelTokenExpired:
        return 'expired', {
            'message': (
                'This cancellation link has expired. Open the event '
                'page to manage your registration.'
            ),
            'event_url': event_url,
        }
    except CancelTokenInvalid:
        return 'invalid', {
            'message': 'This cancellation link is invalid.',
            'event_url': event_url,
        }

    try:
        event = Event.objects.get(slug=slug)
    except Event.DoesNotExist:
        return 'invalid', {
            'message': 'This cancellation link is invalid.',
            'event_url': event_url,
        }

    # Once we have the event, switch to the canonical id+slug URL so
    # the user lands on the new route if they click through.
    event_url = event.get_absolute_url()

    if payload['event_id'] != event.pk:
        return 'invalid', {
            'message': 'This cancellation link is invalid.',
            'event_url': event_url,
        }

    registration = EventRegistration.objects.filter(
        pk=payload['registration_id'],
        event_id=payload['event_id'],
        user_id=payload['user_id'],
    ).first()

    if registration is None:
        return 'already_cancelled', {
            'event': event,
            'event_url': event_url,
            'message': "You're not registered for this event. No action needed.",
        }

    # Issue #713: gate on the time-derived ``is_upcoming`` so a stale
    # ``status='upcoming'`` row whose end has passed lands on the
    # "already started or finished" branch even before the daily cron.
    if not event.is_upcoming:
        return 'event_finished', {
            'event': event,
            'event_url': event_url,
            'registration': registration,
            'message': (
                'This event has already started or finished. '
                'Cancellation is no longer available.'
            ),
        }

    return 'confirm', {
        'event': event,
        'event_url': event_url,
        'registration': registration,
        # Issue #666: render in the registered user's preferred timezone.
        # The visitor here is anonymous (token-authorized) so we look the
        # user up off the registration, NOT ``request.user``.
        'event_datetime': format_user_datetime(
            event.start_datetime, registration.user,
        ),
    }


def cancel_registration_page(request, slug):
    """Render the cancel-registration confirmation page (GET).

    The signed token in the URL is the authorization, so the user does
    NOT need to be signed in. A two-step GET-then-POST flow defeats
    email-prefetch auto-cancellation: the GET is read-only and only
    renders the form; the POST (issued by the visible button) performs
    the actual cancellation.
    """
    token = request.GET.get('token', '')
    state, ctx = _resolve_cancel_state(slug, token)
    ctx['state'] = state
    if state == 'confirm':
        ctx['action_url'] = (
            f'/api/events/{slug}/cancel-registration?token={token}'
        )
    return render(request, 'events/cancel_registration_confirm.html', ctx)


def event_calendar_ics(request, slug):
    """Return the ``.ics`` calendar invite for an event as a downloadable file.

    Issue #484: registered users get a calendar invite by email, but email
    delivery can fail or be filtered. Exposing a stable download URL on the
    event detail page lets the user add the event to their calendar without
    relying on email at all.

    The endpoint is public for non-draft events. The .ics file contains the
    title, start/end time, description, and the join URL — all the same
    information that already shows on the public detail page.
    """
    event = get_object_or_404(Event, slug=slug)
    if event.status == 'draft' and not request.user.is_staff:
        raise Http404

    ics_bytes = generate_ics(event)
    response = HttpResponse(ics_bytes, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="{event.slug}.ics"'
    )
    return response


def _format_http_date(dt):
    """Format ``dt`` as an RFC 7231 IMF-fixdate string (UTC, GMT suffix).

    Django ships ``http_date`` which expects an epoch float; we keep the
    datetime native to avoid a tz conversion round-trip and to make the
    output deterministic across Python versions.
    """
    from django.utils.http import http_date
    return http_date(dt.timestamp())


def _parse_http_date(value):
    """Parse an HTTP-date string into a tz-aware UTC datetime, or None."""
    import datetime
    from email.utils import parsedate_to_datetime
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _build_feed_etag(last_modified, count):
    """Build a weak ETag for the feed.

    Weak (``W/"..."``) because the body is regenerated on each request
    (timestamps in ``DTSTAMP`` change every call) — byte-for-byte
    equality is not guaranteed even when no event row has changed. The
    semantic equivalence the client cares about is captured by
    ``(last_modified, event_count)``.

    Microsecond precision keeps two edits within the same wall-clock
    second from colliding on the same ETag, which would otherwise
    silently 304 the second edit out of subscriber clients.
    """
    if last_modified is None:
        marker = 'empty'
    else:
        # Microseconds since epoch — single integer, monotonic for
        # any later save() and stable for a given (row, save) pair.
        marker = str(int(last_modified.timestamp() * 1_000_000))
    return f'W/"feed-{marker}-{count}"'


def events_calendar_feed(request):
    """Return the subscribable platform-wide events feed.

    Issues #578 / #726. Includes published, non-draft, non-cancelled
    events from the last 30 days through all future, at every tier
    level. Subscribers (Apple Calendar, Google Calendar, Outlook)
    refresh on their own polling cycle; we set short cache headers
    and honor ``If-None-Match`` / ``If-Modified-Since`` so a CDN in
    front can serve 304s when nothing has changed.

    No login required. Tier-gated events (``required_level > 0``)
    appear in the feed with a ``[Members only]`` ``SUMMARY`` prefix
    and a stub ``DESCRIPTION`` (title + members-only sentence +
    detail URL) so visibility is preserved without leaking gated
    bodies into the anonymous feed. A signed-token per-user feed
    for full gated descriptions is a deferred follow-up.
    """
    events_qs = feed_events_queryset()
    events = list(events_qs)
    count = len(events)

    # ``Last-Modified`` is the latest ``updated_at`` across included
    # events; falls back to "now" for an empty queryset so the header
    # is always present and conditional requests still work.
    if events:
        last_modified = max(e.updated_at for e in events)
    else:
        last_modified = timezone.now()

    etag = _build_feed_etag(last_modified, count)

    # Honor conditional requests. ``If-None-Match`` takes precedence
    # over ``If-Modified-Since`` per RFC 7232.
    if_none_match = request.META.get('HTTP_IF_NONE_MATCH', '').strip()
    if if_none_match and if_none_match == etag:
        not_modified = HttpResponse(status=304)
        not_modified['ETag'] = etag
        not_modified['Last-Modified'] = _format_http_date(last_modified)
        not_modified['Cache-Control'] = 'public, max-age=300'
        return not_modified

    if_modified_since = request.META.get('HTTP_IF_MODIFIED_SINCE', '').strip()
    if if_modified_since:
        client_dt = _parse_http_date(if_modified_since)
        if client_dt is not None:
            # Compare at second granularity — HTTP-date has no
            # sub-second component, so anything finer would loop.
            last_modified_floor = last_modified.replace(microsecond=0)
            if client_dt >= last_modified_floor:
                not_modified = HttpResponse(status=304)
                not_modified['ETag'] = etag
                not_modified['Last-Modified'] = _format_http_date(
                    last_modified,
                )
                not_modified['Cache-Control'] = 'public, max-age=300'
                return not_modified

    ics_bytes = generate_feed_ics(events)
    response = HttpResponse(
        ics_bytes, content_type='text/calendar; charset=utf-8',
    )
    # Inline filename hint — subscriber clients fetch this URL on
    # their own schedule, never as a download. ``inline`` keeps
    # browsers from prompting "Save as".
    response['Content-Disposition'] = (
        'inline; filename="ai-shipping-labs.ics"'
    )
    response['Cache-Control'] = 'public, max-age=300'
    response['Last-Modified'] = _format_http_date(last_modified)
    response['ETag'] = etag
    return response
