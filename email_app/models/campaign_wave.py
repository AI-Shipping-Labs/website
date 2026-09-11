from django.conf import settings
from django.db import models


class CampaignWave(models.Model):
    """One operator-released, monitored segment of a re-permission campaign."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        MONITORING = "monitoring", "Monitoring"
        COMPLETE = "complete", "Complete"
        PAUSED = "paused", "Paused"

    campaign = models.ForeignKey(
        "email_app.EmailCampaign",
        on_delete=models.CASCADE,
        related_name="waves",
    )
    number = models.PositiveIntegerField()
    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.PENDING,
        db_index=True,
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="released_campaign_waves",
        null=True,
        blank=True,
    )
    monitoring_started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "number"],
                name="unique_campaign_wave_number",
            ),
            models.CheckConstraint(
                condition=models.Q(number__gt=0),
                name="campaign_wave_number_positive",
            ),
        ]

    def __str__(self):
        return f"{self.campaign_id}:wave-{self.number} ({self.state})"
