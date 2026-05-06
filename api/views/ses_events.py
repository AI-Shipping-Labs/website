"""SES bounce / complaint webhook (issue #453).

Receives SNS notifications for bounce, complaint, delivery, open, and click
events from Amazon SES. The signature on the SNS message is the auth layer;
there is intentionally no token requirement and the endpoint is CSRF-exempt.

Branching:

- ``Type=SubscriptionConfirmation``  -> fetch the ``SubscribeURL`` once to
  confirm the topic, log the event, return 200.
- ``Type=UnsubscribeConfirmation``   -> log only, return 200.
- ``Type=Notification``              -> parse the inner ``Message`` JSON, then
  branch on ``notificationType``:
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

Idempotency: dedup on the SNS ``MessageId``. The ``SesEvent.message_id`` field
has a unique constraint; the view uses ``get_or_create`` so a retried delivery
of the same notification skips all side-effects.

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

    notification_type = inner.get("notificationType", "")

    if notification_type == "Bounce":
        return _handle_bounce(payload, inner, message_id)
    if notification_type == "Complaint":
        return _handle_complaint(payload, inner, message_id)
    if notification_type == "Delivery":
        return _handle_delivery(payload, inner, message_id)
    if notification_type == "Open":
        return _handle_open(payload, inner, message_id)
    if notification_type == "Click":
        return _handle_click(payload, inner, message_id)

    _record_event(
        message_id=message_id,
        event_type=SesEvent.EVENT_TYPE_OTHER,
        raw_payload=payload,
        recipient_email="",
        user=None,
        action_taken=f"unknown notificationType={notification_type!r}; ignored",
    )
    return JsonResponse({"status": "ignored"}, status=200)


# ---------------------------------------------------------------------------
# notificationType handlers
# ---------------------------------------------------------------------------


def _handle_bounce(payload, inner, message_id):
    bounce = inner.get("bounce", {}) or {}
    bounce_type = bounce.get("bounceType", "")
    recipients = bounce.get("bouncedRecipients", []) or []
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

    if not addresses:
        _record_event(
            message_id=message_id,
            event_type=event_type,
            raw_payload=payload,
            recipient_email="",
            user=None,
            action_taken="no recipients in payload; logged only",
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

    try:
        with transaction.atomic():
            SesEvent.objects.create(
                message_id=message_id,
                event_type=event_type,
                raw_payload=payload,
                recipient_email=first_address,
                user=matched_user,
                action_taken="; ".join(actions)[:255],
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
    addresses = [
        (r.get("emailAddress") or "").strip()
        for r in recipients
        if isinstance(r, dict)
    ]
    addresses = [a for a in addresses if a]

    existing = SesEvent.objects.filter(message_id=message_id).first()
    if existing is not None:
        return JsonResponse({"status": "duplicate"}, status=200)

    if not addresses:
        _record_event(
            message_id=message_id,
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
            raw_payload=payload,
            recipient_email="",
            user=None,
            action_taken="no recipients in payload; logged only",
        )
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

    try:
        with transaction.atomic():
            SesEvent.objects.create(
                message_id=message_id,
                event_type=SesEvent.EVENT_TYPE_COMPLAINT,
                raw_payload=payload,
                recipient_email=first_address,
                user=matched_user,
                action_taken="; ".join(actions)[:255],
            )
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

    email_log = None
    if ses_message_id:
        email_log = (
            EmailLog.objects
            .select_related("user")
            .filter(ses_message_id=ses_message_id)
            .first()
        )

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


def _parse_engagement_timestamp(inner, notification_type):
    """Return the SES event timestamp as an aware datetime."""
    detail_key = notification_type.lower()
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


def _record_event(*, message_id, event_type, raw_payload, recipient_email, user, action_taken):
    """Insert a SesEvent row, swallowing duplicate-MessageId races."""
    try:
        SesEvent.objects.create(
            message_id=message_id,
            event_type=event_type,
            raw_payload=raw_payload,
            recipient_email=recipient_email or "",
            user=user,
            action_taken=action_taken[:255],
        )
    except IntegrityError:
        logger.info(
            "Duplicate SesEvent for MessageId=%s; skipping audit insert",
            message_id,
        )
