"""
Event reminder job: checks for events starting in ~24h and ~20 min,
creates reminder notifications for the event audience (deduplicated)
and queues the templated email through the package mail app.

The audience is the shared event audience
(``events.services.event_audience.resolve_event_audience``): the
registrants plus, while ``EVENT_REMINDERS_INCLUDE_COHORT`` is on, the
members of every dated cohort linked to the event's series. Hidden-series
and unpublished events are reminded like any other non-draft,
non-cancelled event. A cohort member's bell and email link to their course
session unit. Complaints, permanent bounces and invalid addresses suppress
the email (the bell stays); a newsletter unsubscribe does not, because a
registration or cohort enrollment is an explicit sign-up.

Called as a background job every 15 minutes via Django-Q2.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from accounts.services.timezones import format_user_datetime

logger = logging.getLogger(__name__)


def _remind_audience(event, interval, title, body_for):
    """Remind every audience member once for ``interval``; return the count.

    ``EventReminderLog`` (event, user, interval) stays the single dedup
    gate, and the audience is deduplicated per user, so a registrant who is
    also in the cohort gets one bell and one email.
    """
    from events.services.event_audience import (
        cohort_session_path,
        email_skip_status,
        resolve_event_audience,
    )
    from integrations.config import event_reminders_include_cohort_enabled
    from notifications.models import EventReminderLog
    from notifications.services.notification_service import NotificationService

    audience = resolve_event_audience(
        event,
        include_cohort=event_reminders_include_cohort_enabled(),
        include_book_club=False,
    )
    already = set(
        EventReminderLog.objects.filter(
            event=event, interval=interval, user__isnull=False,
        ).values_list('user_id', flat=True),
    )
    count = 0
    for member in audience:
        user = member.user
        if user.pk in already:
            continue
        skip = email_skip_status(user, member.signed_up)
        if skip:
            logger.info(
                'Event reminder email suppressed event_id=%s user_id=%s '
                'interval=%s reason=%s',
                event.pk, user.pk, interval, skip,
            )
        event_datetime = format_user_datetime(event.start_datetime, user)
        result = NotificationService.create_event_reminder(
            event=event,
            user=user,
            interval=interval,
            title=title,
            body=body_for(event_datetime),
            url=cohort_session_path(event, user.pk) or None,
            send_email=not skip,
        )
        if result:
            count += 1
    return count


def preview_reminder_audience(event):
    """Dry run of the 24h/20m reminder audience for one event.

    Sends and writes nothing. Lists every recipient with the reasons they
    are included, the link their bell and email open, whether the email
    would be suppressed, and which reminder intervals they already got.
    """
    from events.services.event_audience import (
        cohort_session_url,
        email_skip_status,
        resolve_event_audience,
    )
    from integrations.config import (
        event_reminders_include_cohort_enabled,
        site_base_url,
    )
    from notifications.models import EventReminderLog

    include_cohort = event_reminders_include_cohort_enabled()
    audience = resolve_event_audience(
        event, include_cohort=include_cohort, include_book_club=False,
    )
    sent = {}
    for user_id, interval in EventReminderLog.objects.filter(
        event=event, interval__in=('24h', '20m'), user__isnull=False,
    ).values_list('user_id', 'interval'):
        sent.setdefault(user_id, []).append(interval)
    join_url = f"{site_base_url().rstrip('/')}{event.get_join_url()}"
    results = []
    for member in audience:
        user = member.user
        results.append({
            'user_id': user.pk,
            'email': user.email,
            'reasons': [reason.as_dict() for reason in member.reasons],
            'link': cohort_session_url(event, user.pk) or join_url,
            'email_status': email_skip_status(user, member.signed_up) or 'would_send',
            'already_reminded': sorted(sent.get(user.pk, [])),
        })
    return {
        'event': {'id': event.pk, 'slug': event.slug, 'title': event.title},
        'start_datetime': event.start_datetime.isoformat(),
        'status': event.status,
        'include_cohort': include_cohort,
        'eligible': len(results),
        'would_email': sum(item['email_status'] == 'would_send' for item in results),
        'results': results,
    }


def check_event_reminders():
    """Check for upcoming events and create reminder notifications.

    Runs every 15 minutes. Checks two windows:
    - Events starting in ~24 hours (23h45m to 24h15m from now): bell +
      email + Slack announcement.
    - Events starting in ~20 minutes (15m to 30m from now): bell + email
      only (no Slack — keeps the channel quiet). The window is 15 min wide
      (== the */15 tick interval) so every start-minute is covered by
      exactly one tick (issue #1001).

    Creates deduplicated notifications (and emails) for the event audience
    (registrants plus linked cohort members, see the module docstring) via
    :func:`NotificationService.create_event_reminder`. Posts a Slack
    reminder for the 24h window only (issue #706: 20-min reminders are
    bell + email only to keep #announcements quiet).
    """
    from events.models import Event
    from notifications.models import EventReminderLog
    from notifications.services.slack_announcements import post_slack_announcement

    now = timezone.now()

    # 24-hour reminder window
    window_24h_start = now + timedelta(hours=23, minutes=45)
    window_24h_end = now + timedelta(hours=24, minutes=15)

    # 20-minute reminder window (issue #706 — replaces the prior 1h window).
    # Cron fires every 15 min (issue #1001 restored */15 after #919's hourly
    # cadence broke this window). The window must be at least as wide as the
    # tick interval (15 min) or events whose start-minute falls BETWEEN two
    # consecutive */15 ticks get no reminder: a 10-min-wide window left a
    # ~5-min gap (start-minutes :11-14, :26-29, :41-44, :56-59, ~27% of all
    # starts) uncovered. Widening to 15 min (== the tick interval) guarantees
    # every start-minute is covered by exactly one */15 tick. Where the window
    # happens to span two consecutive ticks (a :00-aligned start), the
    # EventReminderLog unique (event, user, '20m') dedup collapses the overlap
    # to a single reminder.
    window_20m_start = now + timedelta(minutes=15)
    window_20m_end = now + timedelta(minutes=30)

    # Events in 24h window.
    # Issue #713: drop the stored ``status='upcoming'`` clause so a
    # legacy ``status='completed'`` row scheduled in the window still
    # generates reminders. Drafts + cancelled are excluded.
    events_24h = Event.objects.filter(
        start_datetime__gte=window_24h_start,
        start_datetime__lte=window_24h_end,
    ).exclude(status__in=['draft', 'cancelled']).select_related('event_series')

    for event in events_24h:
        count = _remind_audience(
            event,
            '24h',
            f'Reminder: {event.title} starts in 24 hours',
            lambda event_datetime, event=event: (
                f'{event.title} is starting on {event_datetime}. '
                f'Don\'t forget to join!'
            ),
        )

        if count > 0:
            logger.info(
                'Created %d 24h reminders for event %s', count, event.slug,
            )

        # Hidden-series occurrences keep their private bell + email reminders,
        # but never create a channel-post guard. If an operator makes the
        # series public while this occurrence remains in the window, a later
        # tick can still post it once (issue #1832).
        if event.event_series_id and event.event_series.is_hidden:
            continue

        # Post Slack reminder for 24h window, at most once per event.
        # The 24h cron window (30 min wide) overlaps two consecutive
        # 15-min ticks, so without a guard the channel announcement
        # fires twice (issue #887). A per-event EventReminderLog row
        # (user=None, interval='24h_slack') dedups it — mirroring the
        # per-user reminder dedup above. Persist the guard BEFORE the
        # post so a transient Slack failure can't trigger a duplicate
        # on the next tick.
        _, slack_guard_created = EventReminderLog.objects.get_or_create(
            event=event,
            user=None,
            interval='24h_slack',
        )
        if slack_guard_created:
            try:
                post_slack_announcement('event', event)
            except Exception:
                logger.exception(
                    'Failed to post Slack reminder for event %s', event.slug,
                )

    # Events in 20-minute window.
    # Issue #713: drop the stored ``status='upcoming'`` clause; exclude
    # draft + cancelled.
    events_20m = Event.objects.filter(
        start_datetime__gte=window_20m_start,
        start_datetime__lte=window_20m_end,
    ).exclude(status__in=['draft', 'cancelled'])

    for event in events_20m:
        count = _remind_audience(
            event,
            '20m',
            f'Starting soon: {event.title} starts in 20 minutes',
            lambda event_datetime, event=event: (
                f'{event.title} is starting soon! '
                f'Get ready to join at {event_datetime}.'
            ),
        )

        if count > 0:
            logger.info(
                'Created %d 20-min reminders for event %s', count, event.slug,
            )
        # No Slack post for 20-min reminders per spec (issue #706).
