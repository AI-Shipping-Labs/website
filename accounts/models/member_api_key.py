"""Member-owned API keys for the future member API surface."""

import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac


class MemberAPIKey(models.Model):
    """A scoped, hashed API key owned by one member.

    This is deliberately separate from ``accounts.Token``. Operator tokens
    back the staff API surface; member keys are scoped and authenticate only
    through the member API helper in ``accounts.auth``.
    """

    KEY_PREFIX = "asl_member_"
    LOOKUP_PREFIX_LENGTH = 24
    SUPPORTED_SCOPES = frozenset({"plans:read", "plans:write_progress", "plans:write"})
    DEFAULT_SCOPES = ("plans:read", "plans:write_progress", "plans:write")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="member_api_keys",
    )
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=128)
    lookup_prefix = models.CharField(max_length=32, db_index=True)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_ip_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
            models.Index(fields=["lookup_prefix", "revoked_at"]),
        ]

    def clean(self):
        super().clean()
        if not self.name.strip():
            raise ValidationError({"name": "Name is required."})
        scopes = set(self.scopes or [])
        if not scopes:
            raise ValidationError({"scopes": "At least one scope is required."})
        unsupported = scopes - self.SUPPORTED_SCOPES
        if unsupported:
            raise ValidationError(
                {"scopes": f"Unsupported member API key scope: {sorted(unsupported)[0]}."}
            )

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.scopes = sorted(set(self.scopes or self.DEFAULT_SCOPES))
        self.clean()
        super().save(*args, **kwargs)

    @classmethod
    def generate_plaintext_key(cls):
        return f"{cls.KEY_PREFIX}{secrets.token_urlsafe(32)}"

    @classmethod
    def create_for_user(cls, *, user, name, scopes=None):
        plaintext_key = cls.generate_plaintext_key()
        key = cls(
            user=user,
            name=name,
            key_hash=make_password(plaintext_key),
            lookup_prefix=plaintext_key[: cls.LOOKUP_PREFIX_LENGTH],
            scopes=list(scopes or cls.DEFAULT_SCOPES),
        )
        key.save()
        return key, plaintext_key

    @classmethod
    def authenticate(cls, plaintext_key, *, required_scopes=()):
        if not plaintext_key or not plaintext_key.startswith(cls.KEY_PREFIX):
            return None

        lookup_prefix = plaintext_key[: cls.LOOKUP_PREFIX_LENGTH]
        candidates = cls.objects.select_related("user").filter(
            lookup_prefix=lookup_prefix,
            revoked_at__isnull=True,
        )
        required = set(required_scopes or [])
        for candidate in candidates:
            if not check_password(plaintext_key, candidate.key_hash):
                continue
            if required and not required.issubset(set(candidate.scopes or [])):
                return None
            return candidate
        return None

    @property
    def masked_prefix(self):
        return f"{self.lookup_prefix}..."

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])

    def mark_used(self, request):
        ip_hash = ""
        remote_addr = request.META.get("REMOTE_ADDR", "")
        if remote_addr:
            ip_hash = salted_hmac(
                "member-api-key-ip",
                remote_addr,
            ).hexdigest()

        self.last_used_at = timezone.now()
        self.last_used_ip_hash = ip_hash
        self.save(update_fields=["last_used_at", "last_used_ip_hash"])

    def __str__(self):
        return f"{self.user.email} - {self.name}"
