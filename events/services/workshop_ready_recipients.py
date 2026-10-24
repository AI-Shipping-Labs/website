"""Recipient resolution and safe Relay identities for workshop-ready mail."""

from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from accounts.services.email_resolution import normalize_email, resolve_user_by_email

User = get_user_model()


@dataclass(frozen=True)
class WorkshopReadyRecipient:
    email: str
    source: str
    user: object | None = None

    @property
    def key(self):
        if self.user is not None:
            return f"user:{self.user.pk}"
        return f"email:{normalize_email(self.email)}"

    @property
    def email_only(self):
        return self.user is None


def resolve_workshop_ready_recipients(event):
    """Resolve active registrants plus event hosts, deduped by user/email."""
    recipients = []
    seen = set()

    def add(email, source, user=None):
        recipient = _resolve_recipient(email, source, user)
        if recipient is None or recipient.key in seen:
            return
        seen.add(recipient.key)
        recipients.append(recipient)

    registrations = (
        event.registrations.select_related("user")
        .filter(user__is_active=True)
        .order_by("registered_at")
    )
    for registration in registrations:
        add(registration.user.email, "registration", registration.user)
    add(getattr(event, "host_email", ""), "event.host_email")
    host_links = (
        event.event_host_links.select_related("host")
        .filter(host__is_active=True)
        .order_by("position")
    )
    for link in host_links:
        add(link.host.email, "event.hosts")
    return recipients


def _resolve_recipient(email, source, user):
    email = (email or "").strip()
    if not email and user is not None:
        email = (user.email or "").strip()
    if not email:
        return None
    if user is None:
        user = resolve_user_by_email(email)
    if user is not None:
        if not user.is_active:
            return None
        email = user.email
    elif _inactive_primary_user_exists(email) or not _is_deliverable_email(email):
        return None
    return WorkshopReadyRecipient(email=email, source=source, user=user)


def render_recipient_user(recipient):
    if recipient.user is not None:
        return recipient.user
    return SimpleNamespace(
        email=recipient.email,
        first_name="",
        last_name="",
        email_verified=True,
    )


def relay_recipient_identity(recipient):
    if recipient.user is not None:
        return f"user:{recipient.user.pk}"
    normalized = normalize_email(recipient.email)
    digest = sha256(normalized.encode("utf-8")).hexdigest()
    return f"email-sha256:{digest}"


def _is_deliverable_email(email):
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def _inactive_primary_user_exists(email):
    normalized = normalize_email(email)
    if not normalized:
        return False
    return User.objects.filter(email__iexact=normalized, is_active=False).exists()
