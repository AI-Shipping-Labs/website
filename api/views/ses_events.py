"""SES bounce / complaint webhook (issues #453, #495).

Receives SNS notifications for bounce, complaint, delivery, open, and click
events from Amazon SES. The signature on the SNS message is the auth layer;
there is intentionally no token requirement and the endpoint is CSRF-exempt.

Payload shapes supported:

- Identity-notification SNS payloads use ``notificationType``: ``Bounce``,
  ``Complaint``, ``Delivery``, ``Open``, ``Click``. This is what SES emits
  when bounce/complaint notifications are configured directly on the
  verified identity.
- Configuration-set event-publishing payloads use ``eventType``: ``Bounce``,
  ``Complaint``, ``Delivery``, ``Open``, ``Click``, ``Reject``,
  ``Send``, ``DeliveryDelay``, ``RenderingFailure``,
  ``Subscription``. These come through when SES is configured to publish
  events via a configuration-set destination, which is the production
  setup driven by ``SES_CONFIGURATION_SET_NAME``.

In both shapes the inner JSON includes the same ``mail`` block with
``messageId`` and ``destination``, plus the per-event detail block under
its lower-cased name. Bounce/complaint detail keys are identical between
the two shapes.

Branching:

- ``Type=SubscriptionConfirmation``  -> fetch the ``SubscribeURL`` once to
  confirm the topic, log the event, return 200.
- ``Type=UnsubscribeConfirmation``   -> log only, return 200.
- ``Type=Notification``              -> parse the inner ``Message`` JSON, then
  branch on ``notificationType`` / ``eventType``:
    * ``Bounce`` with ``bounceType=Permanent``  -> for each recipient, set
      ``User.unsubscribed=True`` and append the ``bounced`` tag.
    * ``Bounce`` with ``bounceType=Transient``  -> increment
      ``User.soft_bounce_count``. At ``SOFT_BOUNCE_THRESHOLD`` (3), flip
      ``unsubscribed=True``, append ``bounced``, reset the counter to 0.
    * ``Complaint``  -> set ``unsubscribed=True``, append ``complained``.
    * ``Delivery``   -> log only.
    * ``Open``       -> set first-open timestamp and increment open count.
    * ``Click``      -> set first-click timestamp and increment click count;
      also sets first-open timestamp when no open was received.

Correlation (issue #495): bounce and complaint events look up the
matching ``EmailLog`` by inner SES ``mail.messageId`` (when present) and
attach the ``EmailLog`` FK plus normalized bounce diagnostics on both
``SesEvent`` and ``EmailLog`` so staff can trace any bounce back to the
campaign / verification / lead-magnet email that produced it. Events
that do not match an ``EmailLog`` are still audited (no spam-trap loops).

Idempotency: dedup on the SNS ``MessageId``. The ``SesEvent.message_id`` field
has a unique constraint; the view checks for an existing row and bails before
running any side-effects, so a retried delivery of the same notification skips
user mutations and EmailLog updates.

Failure handling: any 4xx/5xx from us causes SNS to retry. Returning 200 on
unmatched recipients is intentional -- a missing user is not a webhook
failure, just a no-op event we still log for audit.
"""

import json
import logging
import urllib.request
from datetime import timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.utils.tags import add_tag
from email_app.models import EmailLog, SesEvent
from integrations.services.ses import validate_sns_notification

logger = logging.getLogger(__name__)

User = get_user_model()

# Number of transient (soft) bounces tolerated before we treat the user as
# permanently bounced. Three matches what most ESPs use for soft-fail
# tolerance: a single "mailbox full" hiccup shouldn't unsubscribe anyone, but
# three consecutive failures is a real signal.
SOFT_BOUNCE_THRESHOLD = 3

TAG_BOUNCED = "bounced"
TAG_COMPLAINED = "complained"


@csrf_exempt
@require_http_methods(["POST"])
def ses_events(request):
    """Webhook entry point for SNS-delivered SES events."""
    raw_body = request.body
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "Invalid payload"}, status=400)

    if not validate_sns_notification(payload):
        logger.warning(
            "SES webhook rejected payload with invalid SNS signature: type=%s message_id=%s",
            payload.get("Type"),
            payload.get("MessageId"),
        )
        return HttpResponse(status=403)

    sns_type = payload.get("Type", "")
    message_id = payload.get("MessageId") or ""

    if not message_id:
        # Without MessageId we can't dedupe. Reject so SNS knows to log it on
        # their side; in practice every real SNS payload carries one.
        return JsonResponse({"error": "Missing MessageId"}, status=400)

    if sns_type == "SubscriptionConfirmation":
        return _handle_subscription_confirmation(payload, message_id)

    if sns_type == "UnsubscribeConfirmation":
        return _handle_unsubscribe_confirmation(payload, message_id)

    if sns_type == "Notification":
        return _handle_notification(payload, message_id)

    # Unknown type: log and accept so SNS doesn't retry forever.
    _record_event(
        message_id=message_id,
        event_type=SesEvent.EVENT_TYPE_OTHER,
        raw_payload=payload,
        recipient_email="",
        user=None,
        action_taken=f"unknown SNS Type={sns_type!r}; ignored",
    )
    return JsonResponse({"status": "ignored"}, status=200)


# ---------------------------------------------------------------------------
# Top-level Type handlers
# ---------------------------------------------------------------------------


def _handle_subscription_confirmation(payload, message_id):
    """Confirm the SNS topic by fetching the SubscribeURL once."""
    existing = SesEvent.objects.filter(message_id=message_id).first()
    if existing is not None:
        return JsonResponse({"status": "duplicate"}, status=200)

    subscribe_url = payload.get("SubscribeURL", "")
    confirmed = False
    if subscribe_url:
        try:
            with urllib.request.urlopen(subscribe_url, timeout=10) as resp:
                # Consume the body so the connection releases cleanly.
                resp.read()
            confirmed = True
            logger.info(
                "Confirmed SNS subscription via SubscribeURL for topic %s",
                payload.get("TopicArn", ""),
            )
        except Exception:
            logger.exception(
                "Failed to fetch SNS SubscribeURL for topic %s",
                payload.get("TopicArn", ""),
            )

    _record_event(
        message_id=message_id,
        event_type=SesEvent.EVENT_TYPE_SUBSCRIPTION_CONFIRMATION,
        raw_payload=payload,
        recipient_email="",
        user=None,
        action_taken=(
            "subscribe_url_fetched" if confirmed else "subscribe_url_fetch_failed"
        ),
    )
    return JsonResponse({"status": "ok"}, status=200)


def _handle_unsubscribe_confirmation(payload, message_id):
    """Log the unsubscription event; no DB mutation needed."""
    logger.info(
        "Received SNS UnsubscribeConfirmation for topic %s",
        payload.get("TopicArn", ""),
    )
    _record_event(
        message_id=message_id,
        event_type=SesEvent.EVENT_TYPE_UNSUBSCRIBE_CONFIRMATION,
        raw_payload=payload,
        recipient_email="",
        user=None,
        action_taken="logged",
    )
    return JsonResponse({"status": "ok"}, status=200)


def _handle_notification(payload, message_id):
    """Parse the inner SES Message and dispatch by notificationType."""
    inner_raw = payload.get("Message", "")
    try:
        inner = json.loads(inner_raw) if isinstance(inner_raw, str) else inner_raw
    except (json.JSONDecodeError, ValueError):
        inner = None

    if not isinstance(inner, dict):
        _record_event(
            message_id=message_id,
            event_type=SesEvent.EVENT_TYPE_OTHER,
            raw_payload=payload,
            recipient_email="",
            user=None,
            action_taken="malformed inner Message; ignored",
        )
        # 200 so SNS doesn't keep retrying a payload that will never parse.
        return JsonResponse({"status": "ignored"}, status=200)

    # SES event publishing (configuration-set destinations) sends ``eventType``;
    # SES identity notifications send ``notificationType``. The two payloads
    # are otherwise identical for the event types we care about, so we
    # normalize to a single ``event_kind`` string and dispatch on it.
    event_kind = (
        inner.get("eventType")
        or inner.get("notificationType")
        or ""
    )

    if event_kind == "Bounce":
        return _handle_bounce(payload, inner, message_id)
    if event_kind == "Complaint":
        return _handle_complaint(payload, inner, message_id)
    if event_kind == "Delivery":
        return _handle_delivery(payload, inner, message_id)
    if event_kind == "Open":
        return _handle_open(payload, inner, message_id)
    if event_kind == "Click":
        return _handle_click(payload, inner, message_id)

    _record_event(
        message_id=message_id,
        event_type=SesEvent.EVENT_TYPE_OTHER,
        raw_payload=payload,
        recipient_email="",
        user=None,
        action_taken=f"unknown event kind={event_kind!r}; ignored",
    )
    return JsonResponse({"status": "ignored"}, status=200)


# ---------------------------------------------------------------------------
# notificationType handlers
# ---------------------------------------------------------------------------


def _handle_bounce(payload, inner, message_id):
    bounce = inner.get("bounce", {}) or {}
    bounce_type = bounce.get("bounceType", "") or ""
    bounce_subtype = bounce.get("bounceSubType", "") or ""
    recipients = bounce.get("bouncedRecipients", []) or []
    diagnostic = _first_recipient_diagnostic(recipients)
    addresses = [
        (r.get("emailAddress") or "").strip()
        for r in recipients
        if isinstance(r, dict)
    ]
    addresses = [a for a in addresses if a]

    if bounce_type == "Permanent":
        event_type = SesEvent.EVENT_TYPE_BOUNCE_PERMANENT
    elif bounce_type == "Transient":
        event_type = SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT
    else:
        event_type = SesEvent.EVENT_TYPE_BOUNCE_OTHER

    # Idempotent insert: if MessageId already exists, do nothing.
    existing = SesEvent.objects.filter(message_id=message_id).first()
    if existing is not None:
        return JsonResponse({"status": "duplicate"}, status=200)

    # Issue #495: correlate to the originating EmailLog by the inner SES
    # mail.messageId. This lets staff trace a bounce back to the specific
    # campaign / verification / lead-magnet send that produced it.
    ses_mail_id = ((inner.get("mail") or {}).get("messageId") or "").strip()
    matched_log = _find_email_log(ses_mail_id)
    bounce_timestamp = _parse_event_timestamp(inner, "bounce")

    if not addresses:
        _record_event(
            message_id=message_id,
            event_type=event_type,
            raw_payload=payload,
            recipient_email="",
            user=None,
            action_taken="no recipients in payload; logged only",
            email_log=matched_log,
            bounce_type=bounce_type,
            bounce_subtype=bounce_subtype,
            diagnostic_code=diagnostic,
        )
        if matched_log is not None:
            _stamp_email_log_bounce(
                matched_log,
                bounce_type=bounce_type,
                bounce_subtype=bounce_subtype,
                diagnostic=diagnostic,
                event_timestamp=bounce_timestamp,
            )
        return JsonResponse({"status": "ok"}, status=200)

    # Single audit row -- captures the first (or only) recipient. With multiple
    # bounced recipients in one notification, the action_taken summarises.
    first_address = addresses[0]
    actions = []
    matched_user = None
    for address in addresses:
        user = _find_user(address)
        if user is None:
            actions.append(f"{address}: no matching user")
            continue
        if matched_user is None:
            matched_user = user
        if bounce_type == "Permanent":
            _mark_permanent_bounce(user)
            actions.append(f"{address}: unsubscribed and tagged {TAG_BOUNCED}")
        elif bounce_type == "Transient":
            new_count, flipped = _record_soft_bounce(user)
            if flipped:
                actions.append(
                    f"{address}: soft bounce threshold reached, "
                    f"unsubscribed and tagged {TAG_BOUNCED}"
                )
            else:
                actions.append(
                    f"{address}: soft_bounce_count={new_count}"
                )
        else:
            actions.append(f"{address}: bounce type {bounce_type!r}; logged only")

    # Prefer the EmailLog's user when correlation succeeded, since the log
    # is the authoritative record of who we sent to. Fall back to the
    # email-address lookup otherwise.
    correlated_user = (
        matched_log.user if matched_log is not None else matched_user
    )

    try:
        with transaction.atomic():
            SesEvent.objects.create(
                message_id=message_id,
                event_type=event_type,
                raw_payload=payload,
                recipient_email=first_address,
                user=correlated_user,
                action_taken="; ".join(actions)[:255],
                email_log=matched_log,
                bounce_type=bounce_type,
                bounce_subtype=bounce_subtype,
                diagnostic_code=diagnostic,
            )
            if matched_log is not None:
                _stamp_email_log_bounce(
                    matched_log,
                    bounce_type=bounce_type,
                    bounce_subtype=bounce_subtype,
                    diagnostic=diagnostic,
                    event_timestamp=bounce_timestamp,
                )
    except IntegrityError:
        # Another worker just wrote the audit row for the same MessageId.
        # Side-effects above are idempotent on retry: ``add_tag`` dedupes,
        # ``unsubscribed=True`` is idempotent. The risky one is
        # ``soft_bounce_count`` -- but we only get here if the SesEvent
        # write lost a race AFTER mutating the user. In practice SNS
        # retries are spaced seconds apart and our writes are fast, so
        # the race is vanishingly small; the duplicate-dedupe check at
        # the top of this function catches the common case.
        logger.warning(
            "Duplicate SesEvent insert for MessageId=%s; user mutations stand",
            message_id,
        )

    return JsonResponse({"status": "ok"}, status=200)


def _handle_complaint(payload, inner, message_id):
    complaint = inner.get("complaint", {}) or {}
    recipients = complaint.get("complainedRecipients", []) or []
    diagnostic = (
        complaint.get("complaintFeedbackType")
        or complaint.get("complaintSubType")
        or ""
    )
    addresses = [
        (r.get("emailAddress") or "").strip()
        for r in recipients
        if isinstance(r, dict)
    ]
    addresses = [a for a in addresses if a]

    existing = SesEvent.objects.filter(message_id=message_id).first()
    if existing is not None:
        return JsonResponse({"status": "duplicate"}, status=200)

    ses_mail_id = ((inner.get("mail") or {}).get("messageId") or "").strip()
    matched_log = _find_email_log(ses_mail_id)
    complaint_timestamp = _parse_event_timestamp(inner, "complaint")

    if not addresses:
        _record_event(
            message_id=message_id,
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
            raw_payload=payload,
            recipient_email="",
            user=None,
            action_taken="no recipients in payload; logged only",
            email_log=matched_log,
            diagnostic_code=diagnostic,
        )
        if matched_log is not None:
            _stamp_email_log_complaint(matched_log, complaint_timestamp)
        return JsonResponse({"status": "ok"}, status=200)

    first_address = addresses[0]
    actions = []
    matched_user = None
    for address in addresses:
        user = _find_user(address)
        if user is None:
            actions.append(f"{address}: no matching user")
            continue
        if matched_user is None:
            matched_user = user
        _mark_complaint(user)
        actions.append(f"{address}: unsubscribed and tagged {TAG_COMPLAINED}")

    correlated_user = (
        matched_log.user if matched_log is not None else matched_user
    )

    try:
        with transaction.atomic():
            SesEvent.objects.create(
                message_id=message_id,
                event_type=SesEvent.EVENT_TYPE_COMPLAINT,
                raw_payload=payload,
                recipient_email=first_address,
                user=correlated_user,
                action_taken="; ".join(actions)[:255],
                email_log=matched_log,
                diagnostic_code=diagnostic,
            )
            if matched_log is not None:
                _stamp_email_log_complaint(matched_log, complaint_timestamp)
    except IntegrityError:
        logger.warning(
            "Duplicate SesEvent insert for MessageId=%s on complaint",
            message_id,
        )

    return JsonResponse({"status": "ok"}, status=200)


def _handle_delivery(payload, inner, message_id):
    delivery = inner.get("delivery", {}) or {}
    addresses = [
        a.strip() for a in (delivery.get("recipients") or []) if isinstance(a, str)
    ]
    addresses = [a for a in addresses if a]
    first_address = addresses[0] if addresses else ""

    _record_event(
        message_id=message_id,
        event_type=SesEvent.EVENT_TYPE_DELIVERY,
        raw_payload=payload,
        recipient_email=first_address,
        user=None,
        action_taken="logged only",
    )
    return JsonResponse({"status": "ok"}, status=200)


def _handle_open(payload, inner, message_id):
    """Record an SES open event against the matching EmailLog."""
    return _handle_engagement(
        payload=payload,
        inner=inner,
        message_id=message_id,
        notification_type="Open",
    )


def _handle_click(payload, inner, message_id):
    """Record an SES click event against the matching EmailLog."""
    return _handle_engagement(
        payload=payload,
        inner=inner,
        message_id=message_id,
        notification_type="Click",
    )


def _handle_engagement(*, payload, inner, message_id, notification_type):
    event_type = (
        SesEvent.EVENT_TYPE_OPEN
        if notification_type == "Open"
        else SesEvent.EVENT_TYPE_CLICK
    )
    event_timestamp = _parse_engagement_timestamp(inner, notification_type)
    ses_message_id = ((inner.get("mail") or {}).get("messageId") or "").strip()
    recipient_email = _first_mail_destination(inner)

    existing = SesEvent.objects.filter(message_id=message_id).first()
    if existing is not None:
        return JsonResponse({"status": "duplicate"}, status=200)

    email_log = _find_email_log(ses_message_id)

    if email_log is None:
        logger.warning(
            "SES %s event for unknown ses_message_id=%s MessageId=%s",
            notification_type,
            ses_message_id,
            message_id,
        )
        _record_event(
            message_id=message_id,
            event_type=event_type,
            raw_payload=payload,
            recipient_email=recipient_email,
            user=None,
            action_taken=(
                f"unknown ses_message_id={ses_message_id!r}; logged only"
            ),
        )
        return JsonResponse({"status": "ok"}, status=200)

    try:
        with transaction.atomic():
            SesEvent.objects.create(
                message_id=message_id,
                event_type=event_type,
                raw_payload=payload,
                recipient_email=recipient_email or email_log.user.email,
                user=email_log.user,
                action_taken=f"{notification_type.lower()} recorded",
                email_log=email_log,
            )
            locked_log = EmailLog.objects.select_for_update().get(pk=email_log.pk)
            if notification_type == "Open":
                updates = {"opens": F("opens") + 1}
                if locked_log.opened_at is None:
                    updates["opened_at"] = event_timestamp
            else:
                updates = {"clicks": F("clicks") + 1}
                if locked_log.clicked_at is None:
                    updates["clicked_at"] = event_timestamp
                if locked_log.opened_at is None:
                    updates["opened_at"] = event_timestamp
            EmailLog.objects.filter(pk=locked_log.pk).update(**updates)
    except IntegrityError:
        logger.info(
            "Duplicate SesEvent for MessageId=%s; skipping engagement update",
            message_id,
        )

    return JsonResponse({"status": "ok"}, status=200)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _find_user(email):
    """Look up a User by email (case-insensitive), or None."""
    if not email:
        return None
    return User.objects.filter(email__iexact=email).first()


def _find_email_log(ses_message_id):
    """Look up the EmailLog that produced this SES message, or None.

    Used by bounce / complaint / engagement handlers to correlate inbound
    events back to the specific transactional or campaign send that
    triggered them.
    """
    if not ses_message_id:
        return None
    return (
        EmailLog.objects
        .select_related("user", "campaign")
        .filter(ses_message_id=ses_message_id)
        .first()
    )


def _parse_engagement_timestamp(inner, notification_type):
    """Return the SES event timestamp as an aware datetime."""
    return _parse_event_timestamp(inner, notification_type.lower())


def _parse_event_timestamp(inner, detail_key):
    """Return the timestamp for an SES event detail block as aware datetime.

    ``detail_key`` is the lower-cased event name, e.g. ``"bounce"``,
    ``"complaint"``, ``"open"``. Falls back to the ``mail.timestamp`` and
    finally to ``timezone.now()`` if neither is parseable.
    """
    raw_timestamp = (
        (inner.get(detail_key) or {}).get("timestamp")
        or (inner.get("mail") or {}).get("timestamp")
    )
    if isinstance(raw_timestamp, str):
        parsed = parse_datetime(raw_timestamp)
        if parsed is not None:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, datetime_timezone.utc)
            return parsed
    return timezone.now()


def _first_mail_destination(inner):
    destinations = (inner.get("mail") or {}).get("destination") or []
    for address in destinations:
        if isinstance(address, str) and address.strip():
            return address.strip()
    return ""


def _first_recipient_diagnostic(recipients):
    """Return the diagnostic / status / reason string from the first
    bounced recipient that has any of those fields, or empty string.

    SES bounce recipients optionally carry ``diagnosticCode`` (the SMTP
    response from the receiving server), ``status`` (RFC 3463 enhanced
    status code), and ``action`` (e.g. ``"failed"``). For ops triage we
    prefer ``diagnosticCode`` since it carries the human-readable
    failure reason; we fall back to a status/action combination if the
    diagnostic is missing.
    """
    for recipient in recipients:
        if not isinstance(recipient, dict):
            continue
        diagnostic = (recipient.get("diagnosticCode") or "").strip()
        if diagnostic:
            return diagnostic
        status = (recipient.get("status") or "").strip()
        action = (recipient.get("action") or "").strip()
        if status or action:
            return f"status={status} action={action}".strip()
    return ""


def _stamp_email_log_bounce(
    email_log, *, bounce_type, bounce_subtype, diagnostic, event_timestamp,
):
    """Persist bounce correlation fields on the matched EmailLog.

    Idempotent: ``bounced_at`` is only written the first time, so SNS
    retries cannot move the timestamp forward. Subsequent bounce events
    for the same EmailLog (rare in practice) update the type / subtype /
    diagnostic so the latest reason wins.
    """
    update_fields = []
    if email_log.bounced_at is None:
        email_log.bounced_at = event_timestamp
        update_fields.append("bounced_at")
    if email_log.bounce_type != bounce_type:
        email_log.bounce_type = bounce_type
        update_fields.append("bounce_type")
    if email_log.bounce_subtype != bounce_subtype:
        email_log.bounce_subtype = bounce_subtype
        update_fields.append("bounce_subtype")
    if diagnostic and email_log.bounce_diagnostic != diagnostic:
        email_log.bounce_diagnostic = diagnostic
        update_fields.append("bounce_diagnostic")
    if update_fields:
        email_log.save(update_fields=update_fields)


def _stamp_email_log_complaint(email_log, event_timestamp):
    """Persist complaint timestamp on the matched EmailLog. Idempotent."""
    if email_log.complained_at is None:
        email_log.complained_at = event_timestamp
        email_log.save(update_fields=["complained_at"])


def _mark_permanent_bounce(user):
    """Flip unsubscribed and tag ``bounced``. Idempotent."""
    if not user.unsubscribed:
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
    add_tag(user, TAG_BOUNCED)


def _mark_complaint(user):
    """Flip unsubscribed and tag ``complained``. Idempotent."""
    if not user.unsubscribed:
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
    add_tag(user, TAG_COMPLAINED)


def _record_soft_bounce(user):
    """Increment soft_bounce_count, flipping at the threshold.

    Returns (new_count_after_write, flipped_to_unsubscribed). When the
    threshold is reached the counter is reset to 0 (so the row is reusable
    if an operator manually clears ``unsubscribed`` later) and the user is
    marked as if they'd permanently bounced.
    """
    user.soft_bounce_count = (user.soft_bounce_count or 0) + 1
    if user.soft_bounce_count >= SOFT_BOUNCE_THRESHOLD:
        user.soft_bounce_count = 0
        user.unsubscribed = True
        user.save(update_fields=["soft_bounce_count", "unsubscribed"])
        add_tag(user, TAG_BOUNCED)
        return 0, True
    user.save(update_fields=["soft_bounce_count"])
    return user.soft_bounce_count, False


def _record_event(
    *,
    message_id,
    event_type,
    raw_payload,
    recipient_email,
    user,
    action_taken,
    email_log=None,
    bounce_type="",
    bounce_subtype="",
    diagnostic_code="",
):
    """Insert a SesEvent row, swallowing duplicate-MessageId races.

    Accepts optional correlation fields (``email_log``, ``bounce_type``,
    ``bounce_subtype``, ``diagnostic_code``) introduced in #495. They
    default to empty so the helper is still safe to call from
    non-bounce/complaint handlers.
    """
    try:
        SesEvent.objects.create(
            message_id=message_id,
            event_type=event_type,
            raw_payload=raw_payload,
            recipient_email=recipient_email or "",
            user=user,
            action_taken=action_taken[:255],
            email_log=email_log,
            bounce_type=bounce_type or "",
            bounce_subtype=bounce_subtype or "",
            diagnostic_code=diagnostic_code or "",
        )
    except IntegrityError:
        logger.info(
            "Duplicate SesEvent for MessageId=%s; skipping audit insert",
            message_id,
        )
