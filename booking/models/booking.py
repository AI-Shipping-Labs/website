import secrets

from django.conf import settings
from django.db import models

from booking.models.host import BookingHost


def generate_manage_token():
    return secrets.token_urlsafe(32)


STATUS_CONFIRMED = "confirmed"
STATUS_CANCELED = "canceled"
STATUS_RESCHEDULED = "rescheduled"

STATUS_CHOICES = [
    (STATUS_CONFIRMED, "Confirmed"),
    (STATUS_CANCELED, "Canceled"),
    (STATUS_RESCHEDULED, "Rescheduled"),
]


class Booking(models.Model):
    """An individual meeting reservation booked with a host."""

    host = models.ForeignKey(
        BookingHost,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    invitee_name = models.CharField(max_length=200)
    invitee_email = models.EmailField()
    invitee_timezone = models.CharField(max_length=64, default="UTC")

    start_datetime = models.DateTimeField(
        help_text="Meeting start time stored in UTC.",
    )
    end_datetime = models.DateTimeField(
        help_text="Meeting end time stored in UTC.",
    )
    duration_minutes = models.IntegerField(default=30)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_CONFIRMED,
    )

    discussion_topic = models.CharField(max_length=255, blank=True, default="")
    discussion_notes = models.TextField(blank=True, default="")
    company = models.CharField(max_length=200, blank=True, default="")

    zoom_meeting_id = models.CharField(max_length=100, blank=True, default="")
    zoom_join_url = models.URLField(max_length=500, blank=True, default="")
    google_event_id = models.CharField(max_length=255, blank=True, default="")

    manage_token = models.CharField(
        max_length=100,
        unique=True,
        default=generate_manage_token,
        help_text="Secret capability token for invitee rescheduling and cancellation.",
    )

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="calendar_bookings",
        help_text="Registered member user matching invitee email if present.",
    )
    booked_call = models.ForeignKey(
        "community.BookedCall",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_booking",
        help_text="Associated CRM BookedCall record.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_datetime"]
        indexes = [
            models.Index(fields=["host", "start_datetime"]),
            models.Index(fields=["status", "start_datetime"]),
            models.Index(fields=["manage_token"]),
        ]

    def __str__(self):
        return f"Booking: {self.invitee_name} ({self.invitee_email}) with {self.host.name} at {self.start_datetime} ({self.status})"

    @property
    def is_active(self):
        return self.status == STATUS_CONFIRMED
