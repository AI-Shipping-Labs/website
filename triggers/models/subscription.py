"""The outbound webhook subscription registry (issue #1070).

A ``TriggerSubscription`` maps an emitted event (``event_type`` plus an
exact-match ``property_filter``) to an external handler URL. When
``emit_event`` records an emission it finds every active subscription
whose filter matches the envelope properties and enqueues a signed async
POST. v0 credits is only the FIRST partner — each future partnership is a
new subscription row, no core changes.
"""

from django.db import models

EVENT_TYPE_CUSTOM = "custom"

# v1 ships a single event type: ``custom`` (emitted by the claim widget).
# Phase 2 adds lifecycle types (``user.registered`` etc.); the field is a
# free CharField so adding a type later needs no migration.
EVENT_TYPE_CHOICES = [
    (EVENT_TYPE_CUSTOM, "Custom (widget-emitted)"),
]


class TriggerSubscription(models.Model):
    """An external handler subscribed to matching emitted events."""

    event_type = models.CharField(
        max_length=100,
        default=EVENT_TYPE_CUSTOM,
        help_text="The event type this subscription listens for (e.g. 'custom').",
    )
    property_filter = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Exact-match map. ALL keys must equal the emitted event's "
            "properties for this subscription to fire. Empty {} matches "
            "every event of this type."
        ),
    )
    target_url = models.URLField(
        max_length=500,
        help_text="The external handler (e.g. a Lambda Function URL).",
    )
    secret = models.CharField(
        max_length=255,
        help_text="HMAC signing secret shared with the handler. Write-only.",
    )
    is_active = models.BooleanField(default=True)
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Operator note describing this subscription.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Trigger subscription"
        verbose_name_plural = "Trigger subscriptions"

    def __str__(self):
        return f"{self.event_type} -> {self.target_url}"

    def matches(self, properties):
        """True if every key in ``property_filter`` equals ``properties``.

        An empty filter matches every event. A non-dict ``properties`` (or
        a filter key missing from the emitted properties) does not match.
        """
        if not self.property_filter:
            return True
        if not isinstance(properties, dict):
            return False
        return all(
            properties.get(key) == value
            for key, value in self.property_filter.items()
        )
