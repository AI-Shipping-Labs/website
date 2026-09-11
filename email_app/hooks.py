"""Transitional site hooks for community_base.mail (plan issue A1.2).

The package owns the durable delivery, the worker and the SES transport.
These hooks keep the site behavior the EmailService path had: the EmailLog
audit row, EmailTemplateOverride precedence, the newsletter opt-out, the
promotional one-click unsubscribe URL and the issue #450 verify-email
footer. Every hook receives package objects, never request state.
"""

from __future__ import annotations

import logging

from community_base.mail.service import MailError

from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    EmailClassificationError,
    classify_email_type,
)
from email_app.services.email_service import (
    EMAIL_TYPES_WITHOUT_VERIFY_FOOTER,
    UNSUBSCRIBED_AT_SEND,
    VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
)

logger = logging.getLogger(__name__)


def preference_resolver(*, purpose, category, to, user):
    """Port of ``EmailService._delivery_decision`` as a package hook.

    Promotional sends respect the global newsletter unsubscribe flag;
    transactional sends stay deliverable for account continuity. Returning
    the reason code (rather than ``False``) keeps the historical skip reason
    visible as the delivery's ``reason_code``. An unknown purpose raises
    ``MailError`` — the callers' soft-fail set — instead of the raw
    classification error, so a typo'd slug degrades the same way it did on
    the EmailService path.
    """

    try:
        email_kind = classify_email_type(purpose)
    except EmailClassificationError as error:
        logger.error("Unknown mail purpose %s", purpose)
        raise MailError(f"unknown mail purpose: {purpose}") from error
    if email_kind == EMAIL_KIND_PROMOTIONAL and getattr(user, "unsubscribed", False):
        return UNSUBSCRIBED_AT_SEND
    return None


def unsubscribe_url_builder(delivery):
    """One-click unsubscribe URL for promotional mail; ``None`` otherwise.

    Transactional mail must not carry an unsubscribe action, mirroring the
    old ``email_kind == EMAIL_KIND_PROMOTIONAL`` gate in ``send``.
    """

    try:
        email_kind = classify_email_type(delivery.purpose)
    except EmailClassificationError:
        return None
    if email_kind != EMAIL_KIND_PROMOTIONAL:
        return None
    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return None

    from accounts.utils.tokens import generate_user_action_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    token = generate_user_action_token(user.pk, "unsubscribe")
    return f"{site_base_url()}/api/unsubscribe?token={token}"


def verify_email_url_builder(delivery):
    """Issue #450 footer link for unverified recipients; ``None`` otherwise.

    The decision runs in the worker against the recipient row as it is
    ``now`` — slightly fresher than the old send-time instance, which the
    old docstring already warned could be stale.
    """

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return None
    if getattr(user, "email_verified", True):
        return None
    if delivery.purpose in EMAIL_TYPES_WITHOUT_VERIFY_FOOTER:
        return None

    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    token = _generate_verification_token(
        user.pk,
        expiry_hours=VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
    )
    return f"{site_base_url()}/api/verify-email?token={token}"


def resolve_auth_mail_context(*, delivery, context):
    """Mint the auth bearer links in the worker, not in the stored context.

    The signup-verification and password-reset callers persist only inputs
    (``return_path``, ``ttl_days``, ``site_url``); the signed URLs are built
    here at delivery time so ``EmailDelivery.context_data`` never retains a
    clickable token (review finding on #1610: the old EmailService path
    persisted neither URL). Every other purpose passes through unchanged.
    """

    if delivery.purpose not in ("email_verification_signup", "password_reset"):
        return context
    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return context

    from accounts.utils.tokens import generate_password_reset_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    if delivery.purpose == "password_reset":
        token = generate_password_reset_token(user, expiry_hours=1)
        context["reset_url"] = f"{site_base_url()}/api/password-reset?token={token}"
        return context

    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415

    token = _generate_verification_token(
        user.pk,
        return_path=context.get("return_path"),
    )
    context["verify_url"] = f"{site_base_url()}/api/verify-email?token={token}"
    return context


def template_override_loader(template_key):
    """``(subject, body_markdown, footer_note)`` from EmailTemplateOverride."""

    from email_app.models import EmailTemplateOverride  # noqa: PLC0415

    override = EmailTemplateOverride.objects.filter(
        template_name=template_key,
    ).first()
    if override is None:
        return None
    return override.subject, override.body_markdown, override.footer_note


def record_send(delivery, rendered, result):
    """Write the EmailLog audit row exactly as EmailService did.

    The package calls this from the worker after provider acceptance, so a
    recorded send now implies provider acceptance. Surrogate recipients
    (no saved user row, issue #703) still produce no Studio-visible row.
    """

    from email_app.models import EmailLog  # noqa: PLC0415

    if delivery.recipient_user_id is None:
        return
    EmailLog.objects.create(
        user_id=delivery.recipient_user_id,
        recipient_email=delivery.recipient_email,
        email_type=delivery.purpose,
        subject=rendered.subject,
        ses_message_id=result.message_id,
        dedupe_key=delivery.idempotency_key,
    )
