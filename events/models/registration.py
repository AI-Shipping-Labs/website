from django.conf import settings
from django.db import models


class EventRegistration(models.Model):
    """Registration of a user for an event."""

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='registrations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_registrations',
    )
    registered_at = models.DateTimeField(auto_now_add=True)
    # Issue #936: per-registration attendance status. Null means
    # "registered but never joined". Non-null is the timestamp of the
    # first live-window join-link click (first-join-wins; see
    # ``event_join_redirect``). This is the deduplicated rollup of the
    # full ``EventJoinClick`` per-click log.
    joined_at = models.DateTimeField(null=True, blank=True, db_index=False)

    class Meta:
        unique_together = [('event', 'user')]
        ordering = ['-registered_at']

    def __str__(self):
        return f'{self.user} - {self.event}'


class SeriesRegistration(models.Model):
    """Standing intent to attend an entire event series (issue #857).

    A ``SeriesRegistration`` is the flag that says "this user wants every
    upcoming occurrence of this series". It does not replace the per-event
    ``EventRegistration`` rows — registering for the series fans out into
    real ``EventRegistration`` rows for every eligible upcoming
    occurrence, so the dashboard, reminders, follow-ups, capacity checks,
    and ``.ics`` invites keep working unchanged.

    The standing flag also lets occurrences added later auto-enroll the
    user via ``enroll_series_registrants_in_event``.
    """

    series = models.ForeignKey(
        'events.EventSeries',
        on_delete=models.CASCADE,
        related_name='series_registrations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='series_registrations',
    )
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('series', 'user')]
        ordering = ['-registered_at']

    def __str__(self):
        return f'{self.user} - {self.series}'
