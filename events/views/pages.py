import calendar as cal_module
import copy
from datetime import date

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from content.access import (
    build_gating_context,
    can_access,
    get_required_tier_name,
)
from content.models import TagRule
from events.models import Event, EventJoinClick, EventRegistration
from payments.models import Tier

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


def _get_tag_rules_for_tags(tags):
    """Return TagRule objects that match any of the given tags.

    Returns dict with 'after_content' and 'sidebar' lists.
    """
    if not tags:
        return {'after_content': [], 'sidebar': []}
    rules = TagRule.objects.filter(tag__in=tags)
    result = {'after_content': [], 'sidebar': []}
    for rule in rules:
        result[rule.position].append(rule)
    return result


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
    }
    return render(request, 'events/events_calendar.html', context)


def events_list(request):
    """Events list page with Upcoming and Past sections.

    Accepts ``?filter=`` with values ``all`` (default), ``upcoming``, or
    ``past``. The past surface is equivalent to the old
    ``/event-recordings`` page: it filters to completed events that have a
    recording URL, supports tag filtering via ``?tag=``, and paginates at
    20 per page.
    """
    # Exclude draft events from public listing
    events = Event.objects.exclude(status='draft')

    filter_mode = request.GET.get('filter', 'all').strip().lower()
    if filter_mode not in VALID_EVENTS_FILTERS:
        filter_mode = 'all'

    selected_tags = _get_selected_tags(request)

    upcoming_events = events.filter(
        status__in=['upcoming', 'live'],
    ).order_by('start_datetime')

    # For the "past" surface we only show completed events with a recording
    # (and honor the ``published`` flag, matching the old /event-recordings
    # list). The default "all" view also includes cancelled events and does
    # not require a recording.
    past_with_recording_qs = events.filter(
        status='completed',
        published=True,
    ).exclude(
        recording_url='',
    ).exclude(
        recording_url__isnull=True,
    ).order_by('-start_datetime')

    past_all_qs = events.filter(
        status__in=['completed', 'cancelled'],
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
        return redirect('event_detail', slug=event.slug)

    # Past events show an unavailable page. The "Back to event" link there
    # already surfaces the inline recording on /events/<slug>.
    if event.status in ('completed', 'cancelled'):
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

    # Record click and redirect
    EventJoinClick.objects.create(event=event, user=request.user)
    return redirect(event.zoom_join_url)


def event_detail(request, slug):
    """Event detail page - always visible to everyone."""
    event = get_object_or_404(Event, slug=slug)
    # Draft events should not be publicly visible
    if event.status == 'draft' and not request.user.is_staff:
        from django.http import Http404
        raise Http404

    user = request.user

    # Check access for registration gating
    has_access = can_access(user, event)

    # Check if user is registered
    is_registered = False
    if user.is_authenticated:
        is_registered = EventRegistration.objects.filter(
            event=event, user=user,
        ).exists()

    # Build gating context. For completed events with a recording, use
    # 'recording' so the CTA reads "watch this recording" instead of
    # "join this event".
    is_completed_recording = (
        event.status == 'completed' and event.has_recording
    )
    gating_content_type = 'recording' if is_completed_recording else 'event'
    gating = build_gating_context(user, event, gating_content_type)

    # Determine if we should show the Zoom join link
    show_zoom_link = (
        is_registered
        and event.can_show_zoom_link()
        and event.status in ('upcoming', 'live')
    )

    # Determine required tier name for CTA
    required_tier_name = get_required_tier_name(event.required_level)

    # Tag-rule components rendered after recording content.
    tag_rules = _get_tag_rules_for_tags(event.tags)

    context = {
        'event': event,
        'has_access': has_access,
        'is_registered': is_registered,
        'show_zoom_link': show_zoom_link,
        'required_tier_name': required_tier_name,
        'tag_rules': tag_rules,
    }
    context.update(gating)
    return render(request, 'events/event_detail.html', context)


def _resolve_tier_payment_link(tier_slug):
    """Return the annual Stripe payment link for a tier slug, or '#' if missing.

    Mirrors how /pricing resolves Stripe links from settings.STRIPE_PAYMENT_LINKS.
    Recap plan cards always link to the annual checkout (matches the Next.js
    reference component behavior).
    """
    if not tier_slug:
        return '#'
    links = settings.STRIPE_PAYMENT_LINKS.get(tier_slug, {})
    return links.get('annual') or links.get('monthly') or '#'


def _annotate_recap_with_tier_links(recap):
    """Walk the recap dict and attach `payment_link` and `tier_name` to any
    section that references a `tier:` key. Returns a deep copy so the original
    JSONField data is not mutated.
    """
    if not recap:
        return recap
    annotated = copy.deepcopy(recap)

    # Build a tier slug -> name map (small table, < 5 rows).
    tier_names = {t.slug: t.name for t in Tier.objects.all()}

    def annotate_cta(cta):
        if not isinstance(cta, dict):
            return
        tier_slug = cta.get('tier')
        if tier_slug:
            cta['payment_link'] = _resolve_tier_payment_link(tier_slug)
            cta['tier_name'] = tier_names.get(tier_slug, tier_slug.title())
            # Default href to the resolved payment link if not explicit
            cta.setdefault('href', cta['payment_link'])

    # Plan cards
    plans = annotated.get('plans') or {}
    for item in plans.get('items', []) or []:
        annotate_cta(item)

    # Early-member section CTAs
    early = annotated.get('early_member') or {}
    annotate_cta(early.get('primary_cta'))
    annotate_cta(early.get('secondary_cta'))

    # Hero CTAs
    hero = annotated.get('hero') or {}
    annotate_cta(hero.get('primary_cta'))
    annotate_cta(hero.get('secondary_cta'))

    return annotated


def event_recap(request, slug):
    """Render the event recap landing page.

    Returns 404 when the event does not exist, is draft (for non-staff),
    or has no recap data.
    """
    event = get_object_or_404(Event, slug=slug)

    if event.status == 'draft' and not request.user.is_staff:
        raise Http404

    if not event.has_recap:
        raise Http404

    recap = _annotate_recap_with_tier_links(event.recap)

    context = {
        'event': event,
        'recap': recap,
    }
    return render(request, 'events/event_recap.html', context)
