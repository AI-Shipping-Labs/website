"""API token model for token-authenticated HTTP endpoints (issue #431).

Tokens are operator-issued (only superusers can mint them) and full-power:
authenticating with a valid token sets ``request.user`` to the token's owner
for that request. There is no scoping or expiration in v1; revocation is the
single explicit way to cut access (deleting the row).

The plaintext key is generated once on save via ``secrets.token_urlsafe(32)``
(~43 base64url characters). It is shown to the operator exactly once on the
Studio one-shot creation page; afterwards the Studio surfaces only an 8-char
masked prefix.
"""

import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Token(models.Model):
    """A long-lived API token belonging to a single user.

    The key itself is the primary key so authentication is a single indexed
    lookup. We don't hash it: tokens are admin-only, scoped to admin accounts,
    and the only place the plaintext is ever rendered is the one-shot Studio
    creation page (the value never leaves the operator's clipboard).
    """

    key = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_tokens",
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Operator-assigned label so superusers can tell tokens apart on "
            "the management page (e.g. 'import script', 'valeriia laptop')."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Updated by token_required on each successful auth.",
    )

    class Meta:
        ordering = ["-created_at"]

    def _token_user_is_staff(self):
        """Return True iff the token owner is eligible for API tokens."""
        if self.user_id is None:
            return False
        if hasattr(self, "_state") and "user" in getattr(self._state, "fields_cache", {}):
            return bool(getattr(self.user, "is_staff", False))

        User = self._meta.get_field("user").remote_field.model
        return User.objects.filter(pk=self.user_id, is_staff=True).exists()

    def clean(self):
        super().clean()
        if not self._token_user_is_staff():
            raise ValidationError(
                {
                    "user": (
                        "API tokens can only be created for staff or admin users."
                    ),
                }
            )

    def save(self, *args, **kwargs):
        """Populate ``key`` with a fresh ``token_urlsafe(32)`` if blank.

        Saving an existing instance with a populated key preserves it.
        """
        self.clean()
        if not self.key:
            self.key = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    @property
    def key_prefix(self):
        """First 8 chars of the key + ellipsis for masked display in Studio."""
        return f"{self.key[:8]}..."

    def __str__(self):
        return f"{self.user.email} - {self.name or self.key_prefix}"
