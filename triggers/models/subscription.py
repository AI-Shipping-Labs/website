"""The outbound webhook subscription registry (issue #1070).

A ``TriggerSubscription`` maps an emitted event (``event_type`` plus an
exact-match ``property_filter``) to an external handler URL. When
``emit_event`` records an emission it finds every active subscription
whose filter matches the envelope properties and enqueues a signed async
POST. v0 credits is only the FIRST partner — each future partnership is a
new subscription row, no core changes.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from triggers.destinations import validate_outbound_url
from triggers.secrets import decrypt_secret, encrypt_secret

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
        validators=[validate_outbound_url],
        help_text="The external handler (e.g. a Lambda Function URL).",
    )
    encrypted_secret = models.TextField(
        blank=True,
        default="",
        db_default="",
        help_text="Encrypted HMAC signing secret. Never render this value.",
    )
    previous_encrypted_secret = models.TextField(blank=True, default="", db_default="")
    secret_version = models.PositiveIntegerField(default=1, db_default=1)
    previous_secret_valid_until = models.DateTimeField(null=True, blank=True)
    legacy_secret = models.CharField(
        max_length=255,
        db_column="secret",
        null=True,
        blank=True,
        editable=False,
        help_text="R1 rollback-only plaintext compatibility shadow.",
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

    def __init__(self, *args, **kwargs):
        plaintext_secret = kwargs.pop("secret", None)
        super().__init__(*args, **kwargs)
        self._secret_was_set = False
        self._loaded_target_url = self.__dict__.get("target_url")
        if plaintext_secret is not None:
            self.set_secret(plaintext_secret)

    @property
    def secret(self):
        """Decrypt the current signing secret only at its point of use."""
        return decrypt_secret(self.encrypted_secret)

    @secret.setter
    def secret(self, value):
        self.set_secret(value)

    def set_secret(self, value, *, grace_period=timedelta(hours=24)):
        """Rotate the secret, retaining the prior version for a bounded grace."""
        if not value:
            raise ValidationError({"secret": "A signing secret is required."})
        if len(value) > 255:
            raise ValidationError({"secret": "Signing secret must be 255 characters or fewer."})
        if self.encrypted_secret:
            self.previous_encrypted_secret = self.encrypted_secret
            self.previous_secret_valid_until = timezone.now() + grace_period
            self.secret_version += 1
        else:
            self.secret_version = max(self.secret_version or 0, 1)
        self.encrypted_secret = encrypt_secret(value)
        self.legacy_secret = value
        self._secret_was_set = True

    def secret_candidates(self):
        """Return current and still-valid previous secrets with their versions."""
        candidates = [(self.secret_version, self.secret)]
        if (
            self.previous_encrypted_secret
            and self.previous_secret_valid_until
            and self.previous_secret_valid_until > timezone.now()
        ):
            candidates.append(
                (self.secret_version - 1, decrypt_secret(self.previous_encrypted_secret)),
            )
        return candidates

    def clean(self):
        super().clean()
        if not self.encrypted_secret:
            raise ValidationError({"secret": "A signing secret is required."})
        if not isinstance(self.property_filter, dict):
            raise ValidationError({"property_filter": "Must be a JSON object (key/value map)."})

    def save(self, *args, **kwargs):
        # Model-level enforcement covers scripts/admin/API, not just ModelForms.
        # Emergency pause writes must remain possible while handler DNS is down.
        excludes = []
        update_fields = kwargs.get("update_fields")
        if self._secret_was_set and update_fields is not None:
            update_fields = set(update_fields) | {
                "encrypted_secret",
                "legacy_secret",
                "previous_encrypted_secret",
                "previous_secret_valid_until",
                "secret_version",
            }
            kwargs["update_fields"] = update_fields
        if self.pk and self.target_url == self._loaded_target_url:
            excludes.append("target_url")
        if update_fields is not None and "target_url" not in update_fields:
            excludes.append("target_url")
        self.full_clean(exclude=set(excludes))
        result = super().save(*args, **kwargs)
        self._secret_was_set = False
        self._loaded_target_url = self.target_url
        return result

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
