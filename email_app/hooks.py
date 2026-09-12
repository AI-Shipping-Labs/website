"""Transitional site hooks for community_base.mail (plan issue A1.2).

The package owns the durable delivery, the worker and the SES transport.
These hooks keep the site behavior the EmailService path had: the EmailLog
audit row, EmailTemplateOverride precedence, the newsletter opt-out, the
promotional one-click unsubscribe URL and the issue #450 verify-email
footer. Every hook receives package objects, never request state.
"""

from __future__ import annotations

import logging

from community_base.jobs.runner import PermanentJobError
from community_base.mail.service import MailError
from django.utils import timezone

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


def _related_event(delivery):
    """Load the delivery's ``events.event`` relation, ``None`` when absent."""

    if delivery.related_object_type != "events.event":
        return None
    from events.models import Event  # noqa: PLC0415

    return Event.objects.filter(pk=delivery.related_object_id).first()


def _resolve_auth_links(delivery, context):
    """Mint the signup-verification and password-reset bearer links."""

    user = delivery.recipient_user
    if user is None or not getattr(user, "pk", None):
        return

    from accounts.utils.tokens import generate_password_reset_token  # noqa: PLC0415
    from integrations.config import site_base_url  # noqa: PLC0415

    if delivery.purpose == "password_reset":
        token = generate_password_reset_token(user, expiry_hours=1)
        context["reset_url"] = f"{site_base_url()}/api/password-reset?token={token}"
        return

    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415

    token = _generate_verification_token(
        user.pk,
        return_path=context.get("return_path"),
    )
    context["verify_url"] = f"{site_base_url()}/api/verify-email?token={token}"


def _resolve_email_change_confirm(delivery, context):
    """Validate the related request and mint its confirm URL.

    Deliveries created before #1613 carry no relation; their stored
    legacy random-token link may finish within the request's original
    24-hour lifetime, so they pass through unchanged. Anything newer
    must present a live, matching ``EmailChangeRequest`` or the send
    fails closed with no token or URL in the error.
    """

    from accounts.models import EmailChangeRequest  # noqa: PLC0415
    from accounts.services.email_change import (  # noqa: PLC0415
        derive_email_change_token,
        hash_email_change_token,
    )
    from integrations.config import site_base_url  # noqa: PLC0415

    if delivery.related_object_type != "accounts.emailchangerequest":
        return

    request_obj = (
        EmailChangeRequest.objects
        .filter(pk=delivery.related_object_id)
        .first()
    )
    if (
        request_obj is None
        or delivery.recipient_user_id is None
        or request_obj.user_id != delivery.recipient_user_id
        or request_obj.new_email != delivery.recipient_email
        or request_obj.confirmed_at is not None
        or request_obj.invalidated_at is not None
    ):
        raise PermanentJobError("email_change_request_unusable")
    if request_obj.expires_at <= timezone.now():
        raise PermanentJobError("email_change_request_expired")
    token = derive_email_change_token(request_obj)
    if request_obj.token_hash != hash_email_change_token(token):
        raise PermanentJobError("email_change_request_token_mismatch")

    context["confirm_url"] = (
        f"{site_base_url().rstrip('/')}"
        f"/account/change-email/confirm?token={token}"
    )


def _resolve_email_changed_notice(delivery, context):
    """Mint the security notice's account link (no binding required)."""

    from integrations.config import site_base_url  # noqa: PLC0415

    context["account_url"] = f"{site_base_url().rstrip('/')}/account/"


def _resolve_privacy_studio_url(delivery, context):
    """Mint the Studio member link for the deletion request to support."""

    if delivery.recipient_user_id is None:
        raise PermanentJobError("privacy_request_user_missing")

    from django.urls import reverse  # noqa: PLC0415

    from integrations.config import site_base_url  # noqa: PLC0415

    context["studio_member_url"] = (
        f"{site_base_url().rstrip('/')}"
        f"{reverse('studio_user_detail', kwargs={'user_id': delivery.recipient_user_id})}"
        "#privacy-deletion-request"
    )


def _resolve_recap_context(delivery, context):
    """Mint recap links from the saved Event at delivery time."""

    from events.services.event_recap_notification import (  # noqa: PLC0415
        absolute_recap_url,
    )
    from integrations.config import site_base_url  # noqa: PLC0415

    event = _related_event(delivery)
    if event is None:
        raise PermanentJobError("recap_event_missing")
    recap_url = absolute_recap_url(event)
    if not recap_url:
        raise PermanentJobError("recap_url_missing")
    context["event_title"] = event.title
    context["recap_url"] = recap_url
    context["event_url"] = (
        f"{site_base_url().rstrip('/')}{event.get_absolute_url()}"
    )


def _resolve_followup_context(delivery, context):
    """Mint the whole follow-up context from the saved Event."""

    from events.services.post_event_mail import (  # noqa: PLC0415
        PostEventMailContextError,
        build_followup_context,
    )

    event = _related_event(delivery)
    if event is None:
        raise PermanentJobError("followup_event_missing")
    try:
        context.update(build_followup_context(event))
    except PostEventMailContextError as error:
        raise PermanentJobError(error.reason) from error


def resolve_auth_mail_context(*, delivery, context):
    """Mint every rendered link in the worker, not in the stored context.

    Auth sends (#1610 slices 1-2), the email-change confirm and notice,
    the privacy deletion request, the recap and the post-event follow-up
    persist only non-secret inputs and relations; this resolver builds
    their URLs at delivery time so ``EmailDelivery.context_data`` never
    retains a clickable link (issue #1613, enforced by the site guard in
    ``email_app.services.context_guard``). Binding failures raise
    ``PermanentJobError`` — a stale relation must fail closed, not
    retry forever — and never name the token or URL. The resolver
    mutates only the in-memory copy the worker passes in; the stored
    context and its idempotency hash stay byte-for-byte unchanged.
    """

    from integrations.config import site_base_url  # noqa: PLC0415

    if delivery.purpose == "account_email_change_confirm":
        _resolve_email_change_confirm(delivery, context)
    elif delivery.purpose == "account_email_changed_notice":
        _resolve_email_changed_notice(delivery, context)
    elif delivery.purpose == "account_deletion_request":
        _resolve_privacy_studio_url(delivery, context)
    elif delivery.purpose == "event_recap_ready":
        _resolve_recap_context(delivery, context)
    elif delivery.purpose == "post_event_followup":
        _resolve_followup_context(delivery, context)
    elif delivery.purpose in (
        "email_verification_signup",
        "password_reset",
        "email_verification_signup_reminder",
        "email_verification_subscribe_reminder",
    ):
        _resolve_auth_links(delivery, context)

    # Render-only injection: templates render absolute links with it,
    # and it never enters the stored context.
    context.setdefault("site_url", site_base_url())
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

    A delivery carrying an ``events.event`` relation (the recap-ready send,
    A1.2 slice 3) keeps the event FK the recap flow attached to its
    ``EmailLog`` rows before the adoption, so the Studio audit surface and
    the event-scoped dedupe queries keep working.
    """

    from email_app.models import EmailLog  # noqa: PLC0415

    if delivery.recipient_user_id is None:
        return
    event_id = None
    if delivery.related_object_type == "events.event":
        event_id = int(delivery.related_object_id)
    EmailLog.objects.create(
        user_id=delivery.recipient_user_id,
        event_id=event_id,
        recipient_email=delivery.recipient_email,
        email_type=delivery.purpose,
        subject=rendered.subject,
        ses_message_id=result.message_id,
        dedupe_key=delivery.idempotency_key,
    )
