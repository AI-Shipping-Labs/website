"""
Event reminder job: checks for events starting in ~24h and ~20 min,
creates reminder notifications for registered users (deduplicated)
and sends the templated email via EmailService.

Called as a background job every 15 minutes via Django-Q2.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def check_event_reminders():
    """Check for upcoming events and create reminder notifications.

    Runs every 15 minutes. Checks two windows:
    - Events starting in ~24 hours (23h45m to 24h15m from now): bell +
      email + Slack announcement.
    - Events starting in ~20 minutes (15m to 25m from now): bell + email
      only (no Slack — keeps the channel quiet).

    Creates deduplicated notifications (and emails) for registered users
    via :func:`NotificationService.create_event_reminder`. Posts a Slack
    reminder for the 24h window only (issue #706: 20-min reminders are
    bell + email only to keep #announcements quiet).
    """
    from events.models import Event, EventRegistration
    from notifications.services.notification_service import NotificationService
    from notifications.services.slack_announcements import post_slack_announcement

    now = timezone.now()

    # 24-hour reminder window
    window_24h_start = now + timedelta(hours=23, minutes=45)
    window_24h_end = now + timedelta(hours=24, minutes=15)

    # 20-minute reminder window (issue #706 — replaces the prior 1h window).
    # Cron fires every 15 min; a 10-min-wide window with 5-min margins
    # guarantees each event is caught exactly once before start.
    window_20m_start = now + timedelta(minutes=15)
    window_20m_end = now + timedelta(minutes=25)

    # Events in 24h window.
    # Issue #713: drop the stored ``status='upcoming'`` clause so a
    # legacy ``status='completed'`` row scheduled in the window still
    # generates reminders. Drafts + cancelled are excluded.
    events_24h = Event.objects.filter(
        start_datetime__gte=window_24h_start,
        start_datetime__lte=window_24h_end,
    ).exclude(status__in=['draft', 'cancelled'])

    for event in events_24h:
        registrations = EventRegistration.objects.filter(
            event=event,
        ).select_related('user')

        count = 0
        for reg in registrations:
            result = NotificationService.create_event_reminder(
                event=event,
                user=reg.user,
                interval='24h',
                title=f'Reminder: {event.title} starts in 24 hours',
                body=f'{event.title} is starting on {event.formatted_start()}. '
                     f'Don\'t forget to join!',
            )
            if result:
                count += 1

        if count > 0:
            logger.info(
                'Created %d 24h reminders for event %s', count, event.slug,
            )

        # Post Slack reminder for 24h window
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
        registrations = EventRegistration.objects.filter(
            event=event,
        ).select_related('user')

        count = 0
        for reg in registrations:
            result = NotificationService.create_event_reminder(
                event=event,
                user=reg.user,
                interval='20m',
                title=f'Starting soon: {event.title} starts in 20 minutes',
                body=f'{event.title} is starting soon! '
                     f'Get ready to join at {event.formatted_start()}.',
            )
            if result:
                count += 1

        if count > 0:
            logger.info(
                'Created %d 20-min reminders for event %s', count, event.slug,
            )
        # No Slack post for 20-min reminders per spec (issue #706).
