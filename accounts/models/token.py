"""Operator API token model for token-authenticated HTTP endpoints.

Tokens are operator-issued (only superusers can mint them) and full-power:
authenticating with a valid token sets ``request.user`` to the token's owner
for that request. There is no scoping or expiration in v1; revocation is the
single explicit way to cut access (deleting the row).

The plaintext credential is generated via ``secrets.token_urlsafe(32)`` and
returned only on the in-memory instance created or rotated during that request.
The database stores a non-secret row identifier, a password-style hash, and a
lookup prefix used to narrow authentication candidates.
"""

import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models


def generate_token_identifier():
    """Return a non-secret stable row identifier for Studio forms/URLs."""
    return uuid.uuid4().hex


class Token(models.Model):
    """A long-lived API token belonging to a single user.

    ``id`` is safe to place in form actions and URLs. The credential itself is
    never stored as plaintext; authentication checks ``lookup_prefix`` matches
    and verifies candidates with ``check_password``.
    """

    LOOKUP_PREFIX_LENGTH = 24

    id = models.CharField(
        max_length=64,
        primary_key=True,
        default=generate_token_identifier,
        editable=False,
    )
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
    key_hash = models.CharField(max_length=128)
    lookup_prefix = models.CharField(max_length=32, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Updated by token_required on each successful auth.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __init__(self, *args, **kwargs):
        # Compatibility bridge for fixtures/tests that still construct
        # ``Token(key="plaintext", ...)``. ``key`` is no longer a model field;
        # treating it as one-shot plaintext keeps bulk-created legacy rows
        # hashed without persisting the supplied value.
        plaintext_key = kwargs.pop("key", None)
        super().__init__(*args, **kwargs)
        self._plaintext_key = None
        if plaintext_key is not None:
            self.set_plaintext_key(plaintext_key)

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
        """Populate the hashed credential when the token is first saved."""
        self.clean()
        if not self.key_hash:
            self.set_plaintext_key(self.generate_plaintext_key())
        super().save(*args, **kwargs)

    @property
    def key(self):
        """Return the one-shot plaintext key for newly created/rotated rows."""
        return self._plaintext_key

    @key.setter
    def key(self, plaintext_key):
        self.set_plaintext_key(plaintext_key)

    @classmethod
    def generate_plaintext_key(cls):
        return secrets.token_urlsafe(32)

    @classmethod
    def create_for_user(cls, *, user, name=""):
        token = cls(user=user, name=name)
        token.save()
        return token, token.key

    def set_plaintext_key(self, plaintext_key):
        plaintext_key = (plaintext_key or "").strip()
        if not plaintext_key:
            raise ValidationError({"key": "API token key cannot be blank."})
        self._plaintext_key = plaintext_key
        self.key_hash = make_password(plaintext_key)
        self.lookup_prefix = plaintext_key[: self.LOOKUP_PREFIX_LENGTH]

    def rotate_key(self):
        """Replace the credential hash and return the new plaintext once."""
        self.set_plaintext_key(self.generate_plaintext_key())
        self.save(update_fields=["key_hash", "lookup_prefix"])
        return self.key

    @classmethod
    def authenticate(cls, plaintext_key):
        if not plaintext_key:
            return None
        lookup_prefix = plaintext_key[: cls.LOOKUP_PREFIX_LENGTH]
        candidates = cls.objects.select_related("user").filter(
            lookup_prefix=lookup_prefix,
        )
        for candidate in candidates:
            if check_password(plaintext_key, candidate.key_hash):
                return candidate
        return None

    @property
    def key_prefix(self):
        """Masked display prefix; not sufficient to authenticate."""
        return f"{self.lookup_prefix[:8]}..."

    def __str__(self):
        return f"{self.user.email} - {self.name or self.key_prefix}"
