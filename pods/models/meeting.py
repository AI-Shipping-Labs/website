"""Agreed pod meetings (issue #1919).

A meeting is stored as a UTC instant (``starts_at``) plus the IANA zone it
was proposed or last moved in (``timezone``). The zone is the anchor for
weekly repeats: every repeat keeps the same local wall-clock time in that
zone, computed per date, so a clock change never shifts the meeting for the
person who picked it. The rules live in ``pods.services.meetings`` so the
member pages, Studio and the staff API share them.
"""

from django.conf import settings
from django.db import models

from content.models.mixins import TimestampedModelMixin
from pods.models.pod import MEETING_MINUTES_CHOICES, POD_SOURCE_CHOICES, Pod

__all__ = [
    'ACTIVE_MEETING_STATUSES',
    'MEETING_RESPONSE_CANT',
    'MEETING_RESPONSE_CHOICES',
    'MEETING_RESPONSE_GOING',
    'MEETING_STATUS_CANCELLED',
    'MEETING_STATUS_CHOICES',
    'MEETING_STATUS_HELD',
    'MEETING_STATUS_PROPOSED',
    'MEETING_STATUS_SCHEDULED',
    'PodMeeting',
    'PodMeetingResponse',
]

MEETING_STATUS_PROPOSED = 'proposed'
MEETING_STATUS_SCHEDULED = 'scheduled'
MEETING_STATUS_HELD = 'held'
MEETING_STATUS_CANCELLED = 'cancelled'
MEETING_STATUS_CHOICES = [
    (MEETING_STATUS_PROPOSED, 'Proposed'),
    (MEETING_STATUS_SCHEDULED, 'Scheduled'),
    (MEETING_STATUS_HELD, 'Held'),
    (MEETING_STATUS_CANCELLED, 'Cancelled'),
]
# A proposed or scheduled meeting can still be changed by members.
ACTIVE_MEETING_STATUSES = (MEETING_STATUS_PROPOSED, MEETING_STATUS_SCHEDULED)

MEETING_RESPONSE_GOING = 'going'
MEETING_RESPONSE_CANT = 'cant_make_it'
MEETING_RESPONSE_CHOICES = [
    (MEETING_RESPONSE_GOING, 'Can make it'),
    (MEETING_RESPONSE_CANT, "Can't make it"),
]


class PodMeeting(TimestampedModelMixin, models.Model):
    """One pod meeting: proposed, scheduled (agreed), held or cancelled."""

    pod = models.ForeignKey(Pod, on_delete=models.CASCADE, related_name='meetings')
    starts_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(choices=MEETING_MINUTES_CHOICES)
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        help_text='IANA zone the time was proposed or last moved in; anchor for weekly repeats.',
    )
    status = models.CharField(
        max_length=20,
        choices=MEETING_STATUS_CHOICES,
        default=MEETING_STATUS_PROPOSED,
    )
    series_id = models.UUIDField(
        null=True,
        blank=True,
        help_text='Shared by the meetings one weekly proposal or schedule created together.',
    )
    created_via = models.CharField(max_length=20, choices=POD_SOURCE_CHOICES)
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='proposed_pod_meetings',
    )
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='moved_pod_meetings',
    )
    moved_at = models.DateTimeField(null=True, blank=True)
    previous_starts_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='status_changed_pod_meetings',
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    # Issue #1935: set on a proposal's head meeting once the daily staff Slack
    # alert announced it as stuck; cleared when the proposal is moved.
    stuck_alerted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['starts_at', 'pk']
        indexes = [
            models.Index(fields=['pod', 'starts_at'], name='pod_meeting_pod_start_idx'),
        ]

    def __str__(self):
        return f'{self.pod} at {self.starts_at:%Y-%m-%d %H:%M} UTC ({self.status})'


class PodMeetingResponse(models.Model):
    """A member's answer to one meeting.

    For a proposed weekly series the proposal-stage answers live on the
    series' first meeting. After agreement a member without a row counts
    as going.
    """

    meeting = models.ForeignKey(PodMeeting, on_delete=models.CASCADE, related_name='responses')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pod_meeting_responses',
    )
    response = models.CharField(max_length=20, choices=MEETING_RESPONSE_CHOICES)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['meeting', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['meeting', 'user'], name='pod_meeting_response_unique_user'),
        ]

    def __str__(self):
        return f'{self.user} -> {self.meeting_id}: {self.response}'
