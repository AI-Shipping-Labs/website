"""Bell reminder before scheduled pod meetings (issue #1919).

The hourly job ``pods-meeting-reminders`` reminds every member of a
``scheduled`` meeting that starts within ``PODS_MEETING_REMINDER_HOURS``,
except members who said they can't make it. ``PodMeeting.reminder_sent_at``
is claimed with a conditional update before anything is sent, so running
twice in an hour (or two workers at once) sends nothing new. A move clears
``reminder_sent_at``, so a moved meeting is reminded again.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_STATUS_SCHEDULED,
    POD_STATUS_ARCHIVED,
    PodMeeting,
)
from pods.services import config as pods_config
from pods.services.meetings import format_in_zone, members_with_zones, notify_members, zone_for

logger = logging.getLogger(__name__)


def send_meeting_reminders(*, now=None):
    """Send due reminders. Returns ``{'meetings': n, 'notifications': m}``."""
    now = now or timezone.now()
    horizon = now + timedelta(hours=pods_config.meeting_reminder_hours())
    due = list(
        PodMeeting.objects.filter(
            status=MEETING_STATUS_SCHEDULED,
            starts_at__gt=now,
            starts_at__lte=horizon,
            reminder_sent_at__isnull=True,
        )
        .exclude(pod__status=POD_STATUS_ARCHIVED)
        .select_related('pod__cohort__course')
        .order_by('starts_at', 'pk')
    )
    meetings = notifications = 0
    for meeting in due:
        claimed = PodMeeting.objects.filter(pk=meeting.pk, reminder_sent_at__isnull=True).update(
            reminder_sent_at=now,
        )
        if not claimed:
            continue
        cant = set(
            meeting.responses.filter(response=MEETING_RESPONSE_CANT).values_list('user_id', flat=True)
        )
        recipients = [user for user in members_with_zones(meeting.pod) if user.pk not in cant]
        notify_members(
            meeting.pod,
            recipients,
            lambda user, meeting=meeting: (
                f'Reminder: {meeting.pod.name} meets {format_in_zone(meeting.starts_at, zone_for(user))}'
            ),
        )
        meetings += 1
        notifications += len(recipients)
    logger.info('Pod meeting reminders: %s meetings, %s notifications', meetings, notifications)
    return {'meetings': meetings, 'notifications': notifications}
