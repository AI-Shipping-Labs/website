import calendar as cal_module
import copy
from datetime import date

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from content.access import (
    build_gating_context,
    can_access,
    get_required_tier_name,
)
from events.models import Event, EventJoinClick, EventRegistration
from payments.models import Tier


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
    """Events calendar page with Upcoming and Past sections."""
    # Exclude draft events from public listing
    events = Event.objects.exclude(status='draft')

    upcoming_events = events.filter(
        status__in=['upcoming', 'live'],
    ).order_by('start_datetime')

    past_events = events.filter(
        status__in=['completed', 'cancelled'],
    ).order_by('-start_datetime')

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
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'registered_event_ids': registered_event_ids,
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

    # Past events show an unavailable page
    if event.status in ('completed', 'cancelled'):
        recording_url = ''
        if event.has_recording:
            recording_url = event.get_recording_url()
        return render(request, 'events/join_unavailable.html', {
            'event': event,
            'reason': 'past',
            'recording_url': recording_url,
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

    # Build gating context for unauthorized users
    gating = build_gating_context(user, event, 'event')

    # Determine if we should show the Zoom join link
    show_zoom_link = (
        is_registered
        and event.can_show_zoom_link()
        and event.status in ('upcoming', 'live')
    )

    # Determine required tier name for CTA
    required_tier_name = get_required_tier_name(event.required_level)

    context = {
        'event': event,
        'has_access': has_access,
        'is_registered': is_registered,
        'show_zoom_link': show_zoom_link,
        'required_tier_name': required_tier_name,
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
