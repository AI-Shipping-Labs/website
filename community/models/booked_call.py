"""BookedCall model for "Request a call" Phase 2 (#884).

A lightweight record of a 1:1 call booked through Calendly, captured from
the Calendly ``invitee.created`` webhook. Each row links the booking back
to a :class:`CallHost` and (when matched by email) the member's user, so
the booked call surfaces on the member's CRM record (#871) and so host
availability (``CallHost.current_load``) stays accurate without manual
staff edits.

The Calendly event URI is stored as a stable idempotency key: webhook
re-deliveries upsert the same row instead of creating duplicates or
double-counting capacity.
"""

from django.conf import settings
from django.db import models

STATUS_BOOKED = 'booked'
STATUS_CANCELED = 'canceled'

STATUS_CHOICES = [
    (STATUS_BOOKED, 'Booked'),
    (STATUS_CANCELED, 'Canceled'),
]


class BookedCall(models.Model):
    """A call booked via Calendly, captured from the webhook.

    ``member`` is nullable: a booking can arrive for an email that does
    not match any user (e.g. a prospect). Such bookings still count
    against the host's capacity and are logged, they simply don't appear
    on a CRM record until a matching user exists.
    """

    host = models.ForeignKey(
        'community.CallHost',
        on_delete=models.CASCADE,
        related_name='booked_calls',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='booked_calls',
        help_text='The member who booked, matched by invitee email when known.',
    )
    invitee_email = models.EmailField(
        help_text='Email the invitee booked with (raw, before user matching).',
    )
    invitee_name = models.CharField(max_length=200, blank=True, default='')
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Start time of the booked call (from the Calendly event).',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_BOOKED,
    )
    calendly_event_uri = models.CharField(
        max_length=500,
        unique=True,
        help_text=(
            'Calendly scheduled-event URI. Stable idempotency key so '
            'webhook re-deliveries never double-count capacity.'
        ),
    )
    calendly_invitee_uri = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Calendly invitee URI (used to match cancellations).',
    )
    reschedule_url = models.URLField(max_length=500, blank=True, default='')
    cancel_url = models.URLField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-scheduled_at', '-created_at']
        indexes = [
            models.Index(fields=['member', 'status']),
            models.Index(fields=['host', 'status']),
        ]

    def __str__(self):
        who = self.member.email if self.member_id else self.invitee_email
        return f'BookedCall({who} with {self.host.name}, {self.status})'

    @property
    def is_active(self):
        """True for a booking that still consumes a slot."""
        return self.status == STATUS_BOOKED
