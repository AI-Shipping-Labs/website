from django.conf import settings
from django.db import models

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DECLINED = "declined"

STATUS_CHOICES = [
    (STATUS_PENDING, "Pending"),
    (STATUS_APPROVED, "Approved"),
    (STATUS_DECLINED, "Declined"),
]


class CallRequest(models.Model):
    """Member request for an additional 1:1 call with a host."""

    STATUS_PENDING = STATUS_PENDING
    STATUS_APPROVED = STATUS_APPROVED
    STATUS_DECLINED = STATUS_DECLINED
    STATUS_CHOICES = STATUS_CHOICES

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="call_requests",
    )
    host = models.ForeignKey(
        "booking.BookingHost",
        on_delete=models.CASCADE,
        related_name="call_requests",
    )
    topic = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    review_notes = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_call_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["member", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"CallRequest from {self.member} ({self.topic}) - {self.status}"
