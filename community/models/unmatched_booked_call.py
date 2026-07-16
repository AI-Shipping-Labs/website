"""R1 staging for Calendly calls that cannot yet be attached to a host."""

from django.conf import settings
from django.db import models

from .booked_call import STATUS_BOOKED, STATUS_CHOICES


class UnmatchedBookedCall(models.Model):
    """Preserve an unmatched Calendly call outside the legacy-visible table."""

    source_booked_call_id = models.BigIntegerField(null=True, blank=True, unique=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='unmatched_booked_calls',
    )
    invitee_email = models.EmailField(blank=True, default='')
    invitee_name = models.CharField(max_length=200, blank=True, default='')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_BOOKED,
    )
    calendly_event_uri = models.CharField(max_length=500, unique=True)
    calendly_invitee_uri = models.CharField(
        max_length=500,
        blank=True,
        default='',
        db_index=True,
    )
    scheduling_url = models.URLField(max_length=500, blank=True, default='')
    reschedule_url = models.URLField(max_length=500, blank=True, default='')
    cancel_url = models.URLField(max_length=500, blank=True, default='')
    canceled_at = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-scheduled_at', '-created_at']

    def __str__(self):
        return f'UnmatchedBookedCall({self.invitee_email}, {self.status})'
