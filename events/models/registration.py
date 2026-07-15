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


class HostInviteDelivery(models.Model):
    """Durable, bounded delivery state for one host's initial invitation."""

    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENDING, 'Sending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
    ]
    MAX_ATTEMPTS = 3
    ERROR_PROVIDER = 'provider_error'
    ERROR_TIMEOUT = 'provider_timeout'
    ERROR_CONNECTION = 'provider_connection_error'
    ERROR_CONFIGURATION = 'provider_configuration_error'
    ERROR_UNAVAILABLE = 'provider_unavailable'
    ERROR_REJECTED = 'provider_rejected'
    ERROR_MESSAGES = {
        ERROR_PROVIDER: 'Delivery failed; review application logs.',
        ERROR_TIMEOUT: 'Delivery provider timed out.',
        ERROR_CONNECTION: 'Could not connect to the delivery provider.',
        ERROR_CONFIGURATION: 'Delivery provider configuration is incomplete.',
        ERROR_UNAVAILABLE: 'Delivery provider is temporarily unavailable.',
        ERROR_REJECTED: 'Delivery provider rejected the message.',
    }

    event = models.ForeignKey(
        'events.Event', on_delete=models.CASCADE,
        related_name='host_invite_deliveries',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='host_invite_deliveries',
    )
    access_version = models.UUIDField()
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_ics_sequence = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default='')
    email_log = models.ForeignKey(
        'email_app.EmailLog', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'user', 'access_version'],
                name='unique_host_invite_delivery',
            ),
        ]

    @property
    def operator_error_message(self):
        """Return only allowlisted diagnostics for the Studio surface.

        Unknown values include legacy rows that may contain raw exception
        messages. They deliberately collapse to the generic category rather
        than being rendered back to an operator.
        """
        if not self.last_error:
            return ''
        return self.ERROR_MESSAGES.get(
            self.last_error,
            self.ERROR_MESSAGES[self.ERROR_PROVIDER],
        )


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
