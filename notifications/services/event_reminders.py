"""
Event reminder job: checks for events starting in ~24h and ~1h,
creates reminder notifications for registered users (deduplicated).

Called as a background job every 15 minutes via Django-Q2.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def check_event_reminders():
    """Check for upcoming events and create reminder notifications.

    Runs every 15 minutes. Checks two windows:
    - Events starting in ~24 hours (23.75h to 24.25h from now)
    - Events starting in ~1 hour (0.75h to 1.25h from now)

    Creates deduplicated notifications for registered users.
    Posts Slack reminder for 24h window only (per spec: 1h = no Slack).
    """
    from events.models import Event, EventRegistration
    from notifications.services.notification_service import NotificationService
    from notifications.services.slack_announcements import post_slack_announcement

    now = timezone.now()

    # 24-hour reminder window
    window_24h_start = now + timedelta(hours=23, minutes=45)
    window_24h_end = now + timedelta(hours=24, minutes=15)

    # 1-hour reminder window
    window_1h_start = now + timedelta(minutes=45)
    window_1h_end = now + timedelta(hours=1, minutes=15)

    # Events in 24h window
    events_24h = Event.objects.filter(
        status='upcoming',
        start_datetime__gte=window_24h_start,
        start_datetime__lte=window_24h_end,
    )

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

    # Events in 1h window
    events_1h = Event.objects.filter(
        status='upcoming',
        start_datetime__gte=window_1h_start,
        start_datetime__lte=window_1h_end,
    )

    for event in events_1h:
        registrations = EventRegistration.objects.filter(
            event=event,
        ).select_related('user')

        count = 0
        for reg in registrations:
            result = NotificationService.create_event_reminder(
                event=event,
                user=reg.user,
                interval='1h',
                title=f'Starting soon: {event.title} starts in 1 hour',
                body=f'{event.title} is starting soon! '
                     f'Get ready to join at {event.formatted_start()}.',
            )
            if result:
                count += 1

        if count > 0:
            logger.info(
                'Created %d 1h reminders for event %s', count, event.slug,
            )
        # No Slack post for 1h reminders per spec
