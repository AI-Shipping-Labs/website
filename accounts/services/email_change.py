"""Self-service login email change flow for member accounts."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.gating import is_newsletter_only_user
from accounts.models import EmailAlias, EmailChangeRequest, User
from accounts.services.email_resolution import normalize_email
from email_app.services.email_service import EmailService
from integrations.config import site_base_url

EMAIL_CHANGE_TOKEN_BYTES = 32
EMAIL_CHANGE_EXPIRY_HOURS = 24
EMAIL_CHANGE_REQUEST_THROTTLE_SECONDS = 60
EMAIL_CHANGE_CONFIRM_TEMPLATE = "account_email_change_confirm"
EMAIL_CHANGED_NOTICE_TEMPLATE = "account_email_changed_notice"

GENERIC_EMAIL_UNUSABLE_ERROR = "That email cannot be used for this account."
GENERIC_CONFIRM_ERROR = (
    "This email change link is no longer valid. Go to Account to request a new link."
)
EXPIRED_CONFIRM_ERROR = (
    "This email change link expired. Go to Account to request a new link."
)


class EmailChangeError(ValueError):
    """Base class for expected member-facing email-change errors."""

    message = "We could not start that email change."
    code = "email_change_error"
    status = 400

    def __init__(self, message=None):
        super().__init__(message or self.message)
        self.message = message or self.message


class EmailChangePasswordError(EmailChangeError):
    message = "Enter your current password to change your login email."
    code = "invalid_password"


class EmailChangeValidationError(EmailChangeError):
    message = GENERIC_EMAIL_UNUSABLE_ERROR
    code = "invalid_email"


class EmailChangeThrottleError(EmailChangeError):
    message = (
        "We just sent a verification link for that email. "
        "Please wait a minute before requesting another."
    )
    code = "throttled"
    status = 429


@dataclass(frozen=True)
class EmailChangeConfirmationResult:
    success: bool
    message: str
    status: str
    user: User | None = None
    old_email: str = ""
    new_email: str = ""


def hash_email_change_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def active_email_change_request_for_user(user):
    return (
        EmailChangeRequest.objects
        .filter(
            user=user,
            confirmed_at__isnull=True,
            invalidated_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )


def _validate_new_email_for_user(user, raw_email):
    new_email = normalize_email(raw_email)
    if not new_email:
        raise EmailChangeValidationError("Enter a valid email address.")

    try:
        validate_email(new_email)
    except ValidationError as exc:
        raise EmailChangeValidationError("Enter a valid email address.") from exc

    current_email = normalize_email(user.email)
    if new_email == current_email:
        raise EmailChangeValidationError(
            "Enter a different email from your current login email."
        )

    primary_owner = (
        User.objects
        .filter(email__iexact=new_email, is_active=True)
        .exclude(pk=user.pk)
        .first()
    )
    if primary_owner is not None:
        raise EmailChangeValidationError()

    alias = EmailAlias.objects.select_related("user").filter(email=new_email).first()
    if alias is not None and alias.user_id != user.pk:
        raise EmailChangeValidationError()

    return new_email


def _enforce_password(user, current_password):
    if not user.has_usable_password():
        return
    if not current_password or not user.check_password(current_password):
        raise EmailChangePasswordError()


def _enforce_throttle(user, new_email):
    cache_key = f"email-change-request:{user.pk}:{new_email}"
    if not cache.add(cache_key, "1", EMAIL_CHANGE_REQUEST_THROTTLE_SECONDS):
        raise EmailChangeThrottleError()


def _build_confirm_url(token):
    return f"{site_base_url()}/account/change-email/confirm?token={token}"


def _send_confirm_email(user, request_obj, token):
    EmailService().send(
        user,
        EMAIL_CHANGE_CONFIRM_TEMPLATE,
        {
            "old_email": request_obj.old_email,
            "new_email": request_obj.new_email,
            "confirm_url": _build_confirm_url(token),
            "expiry_hours": EMAIL_CHANGE_EXPIRY_HOURS,
        },
        recipient_email=request_obj.new_email,
    )


def request_email_change(user, raw_new_email, current_password=None, *, send=True):
    """Create a latest pending email-change request and send its link.

    Returns ``(EmailChangeRequest, plaintext_token)``. The token is returned so
    tests and immediate callers can build the email link; only its hash is
    persisted.
    """
    if is_newsletter_only_user(user):
        raise EmailChangeValidationError()

    _enforce_password(user, current_password)
    new_email = _validate_new_email_for_user(user, raw_new_email)
    _enforce_throttle(user, new_email)

    now = timezone.now()
    token = secrets.token_urlsafe(EMAIL_CHANGE_TOKEN_BYTES)
    token_hash = hash_email_change_token(token)

    with transaction.atomic():
        (
            EmailChangeRequest.objects
            .filter(
                user=user,
                confirmed_at__isnull=True,
                invalidated_at__isnull=True,
            )
            .update(invalidated_at=now)
        )
        request_obj = EmailChangeRequest.objects.create(
            user=user,
            old_email=normalize_email(user.email),
            new_email=new_email,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=EMAIL_CHANGE_EXPIRY_HOURS),
            last_sent_at=now,
        )

    if send:
        try:
            _send_confirm_email(user, request_obj, token)
        except Exception:
            cache.delete(f"email-change-request:{user.pk}:{new_email}")
            EmailChangeRequest.objects.filter(pk=request_obj.pk).update(
                invalidated_at=timezone.now(),
            )
            raise

    return request_obj, token


def _email_is_available_for_confirmation(user, new_email):
    primary_owner = (
        User.objects
        .filter(email__iexact=new_email, is_active=True)
        .exclude(pk=user.pk)
        .first()
    )
    if primary_owner is not None:
        return False

    alias = EmailAlias.objects.filter(email=new_email).first()
    return alias is None or alias.user_id == user.pk


def _sync_allauth_email_addresses(user, *, old_email, new_email):
    try:
        from allauth.account.models import EmailAddress
    except Exception:
        return

    EmailAddress.objects.filter(user=user, primary=True).update(primary=False)

    new_record, _ = EmailAddress.objects.get_or_create(
        user=user,
        email=new_email,
        defaults={"verified": True, "primary": True},
    )
    changed = []
    if not new_record.verified:
        new_record.verified = True
        changed.append("verified")
    if not new_record.primary:
        new_record.primary = True
        changed.append("primary")
    if changed:
        new_record.save(update_fields=changed)

    old_record = EmailAddress.objects.filter(
        user=user,
        email__iexact=old_email,
    ).exclude(pk=new_record.pk).first()
    if old_record is not None and old_record.primary:
        old_record.primary = False
        old_record.save(update_fields=["primary"])


def _ensure_former_email_alias(user, old_email):
    if EmailAlias.objects.filter(user=user, email=old_email).exists():
        return
    EmailAlias.objects.get_or_create(
        email=old_email,
        defaults={
            "user": user,
            "source": EmailAlias.SOURCE_ACCOUNT_CHANGE,
            "note": "Former login email preserved after member email change.",
            "created_by": None,
        },
    )


def _send_old_email_notice(user, *, old_email, new_email):
    EmailService().send(
        user,
        EMAIL_CHANGED_NOTICE_TEMPLATE,
        {
            "old_email": old_email,
            "new_email": new_email,
            "account_url": f"{site_base_url()}/account/",
        },
        recipient_email=old_email,
    )


def confirm_email_change(token):
    token = (token or "").strip()
    if not token:
        return EmailChangeConfirmationResult(
            success=False,
            message=GENERIC_CONFIRM_ERROR,
            status="malformed",
        )

    token_hash = hash_email_change_token(token)
    now = timezone.now()

    with transaction.atomic():
        request_obj = (
            EmailChangeRequest.objects
            .select_for_update()
            .select_related("user")
            .filter(token_hash=token_hash)
            .first()
        )
        if request_obj is None:
            return EmailChangeConfirmationResult(
                success=False,
                message=GENERIC_CONFIRM_ERROR,
                status="malformed",
            )
        if request_obj.confirmed_at is not None:
            return EmailChangeConfirmationResult(
                success=False,
                message=GENERIC_CONFIRM_ERROR,
                status="reused",
            )
        if request_obj.invalidated_at is not None:
            return EmailChangeConfirmationResult(
                success=False,
                message=GENERIC_CONFIRM_ERROR,
                status="superseded",
            )
        if request_obj.expires_at <= now:
            return EmailChangeConfirmationResult(
                success=False,
                message=EXPIRED_CONFIRM_ERROR,
                status="expired",
            )

        user = User.objects.select_for_update().get(pk=request_obj.user_id)
        old_email = request_obj.old_email
        new_email = request_obj.new_email

        if not _email_is_available_for_confirmation(user, new_email):
            return EmailChangeConfirmationResult(
                success=False,
                message=GENERIC_CONFIRM_ERROR,
                status="collision",
            )

        EmailAlias.objects.filter(user=user, email=new_email).delete()

        user.email = new_email
        user.email_verified = True
        user.verification_expires_at = None
        user.slack_checked_at = None
        user.save(
            update_fields=[
                "email",
                "email_verified",
                "verification_expires_at",
                "slack_checked_at",
            ]
        )

        try:
            _sync_allauth_email_addresses(
                user,
                old_email=old_email,
                new_email=new_email,
            )
            _ensure_former_email_alias(user, old_email)
        except IntegrityError:
            transaction.set_rollback(True)
            return EmailChangeConfirmationResult(
                success=False,
                message=GENERIC_CONFIRM_ERROR,
                status="collision",
            )

        request_obj.confirmed_at = now
        request_obj.save(update_fields=["confirmed_at"])

    _send_old_email_notice(user, old_email=old_email, new_email=new_email)

    return EmailChangeConfirmationResult(
        success=True,
        message="Your account email was changed successfully.",
        status="confirmed",
        user=user,
        old_email=old_email,
        new_email=new_email,
    )
