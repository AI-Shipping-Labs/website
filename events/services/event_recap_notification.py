"""Explicit recap-ready delivery for event registrants (issue #1557)."""

import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from email_app.models import EmailLog, SesEvent
from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration
from events.models.event import PUBLIC_EVENT_STATUSES
from events.services.calendar_lifecycle import user_has_permanent_bounce
from integrations.config import site_base_url
from notifications.models import EventReminderLog, Notification

logger = logging.getLogger(__name__)
User = get_user_model()

EMAIL_TYPE = "event_recap_ready"
NOTIFICATION_TYPE = "event_recap"
INTERVAL_EMAIL = "recap_email"
INTERVAL_IN_APP = "recap_in_app"


class EventRecapNotReady(ValueError):
    """Raised when a recap is not public and safe to announce."""

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class DeliveryState:
    status: str
    identifier: int | None = None


def _not_ready(reason, message):
    raise EventRecapNotReady(reason, message)


def assert_recap_ready(event):
    """Return the relative canonical recap URL when anonymous access is ready.

    This mirrors the public recap view's derived state. There is deliberately
    no stored "announced" or "published recap" flag: recap publication remains
    the combination of content, event timing, public event state, and the
    existing canonical route.
    """
    if not event.has_recap:
        _not_ready("missing_recap", "Add non-empty recap content first.")
    if event.status == "draft":
        _not_ready("event_draft", "Draft events cannot announce a recap.")
    if event.status == "cancelled":
        _not_ready(
            "event_cancelled",
            "Cancelled events cannot announce a recap.",
        )
    if not event.published:
        _not_ready(
            "event_unpublished",
            "Publish the event before announcing its recap.",
        )
    if event.status not in PUBLIC_EVENT_STATUSES:
        _not_ready(
            "event_not_public",
            "The event status is not publicly visible.",
        )
    if not event.is_past:
        _not_ready(
            "event_not_ended",
            "The recap can be announced once the event has ended.",
        )

    recap_url = event.get_recap_url()
    if not recap_url:
        _not_ready(
            "recap_url_missing",
            "The canonical public recap URL is not available.",
        )
    return recap_url


def absolute_recap_url(event, recap_path=None):
    """Return the canonical absolute recap URL used by both channels."""
    recap_path = recap_path or event.get_recap_url()
    if not recap_path:
        return ""
    return f"{site_base_url().rstrip('/')}{recap_path}"


def _active_registrations(event):
    """Return the exact event audience at audience-build time."""
    return (
        EventRegistration.objects
        .filter(event_id=event.pk, user__is_active=True)
        .select_related("user")
        .order_by("pk")
    )


def recap_ready_state(event):
    """Return the operator-facing readiness and idempotency counters."""
    try:
        recap_path = assert_recap_ready(event)
    except EventRecapNotReady as exc:
        return {
            "available": False,
            "reason": str(exc),
            "reason_code": exc.reason,
            "recap_url": absolute_recap_url(event),
            "eligible_count": 0,
            "already_emailed_count": 0,
            "already_notified_count": 0,
        }

    registrations = _active_registrations(event)
    user_ids = [registration.user_id for registration in registrations]
    email_count = EventReminderLog.objects.filter(
        event_id=event.pk,
        user_id__in=user_ids,
        interval=INTERVAL_EMAIL,
    ).count() if user_ids else 0
    in_app_count = EventReminderLog.objects.filter(
        event_id=event.pk,
        user_id__in=user_ids,
        interval=INTERVAL_IN_APP,
    ).count() if user_ids else 0
    return {
        "available": True,
        "reason": "",
        "reason_code": "",
        "recap_url": absolute_recap_url(event, recap_path),
        "eligible_count": len(user_ids),
        "already_emailed_count": email_count,
        "already_notified_count": in_app_count,
    }


def _current_registered_user(event, user_id):
    """Re-check registration and account activity immediately before delivery."""
    if not EventRegistration.objects.filter(
        event_id=event.pk,
        user_id=user_id,
    ).exists():
        return None, "skipped_registration"
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None, "skipped_inactive"
    return user, ""


def _dedupe_key(event, user):
    return f"event-recap-ready:{event.pk}:{user.pk}"


def _user_has_complaint(user):
    """Return whether SES has complained about this user's address.

    Complaints share the legacy ``unsubscribed`` flag with voluntary
    newsletter opt-outs, so that flag alone cannot suppress this
    transactional message without violating the event-registration policy.
    Keep the provider-safety check anchored to the complaint audit fields.
    """
    return (
        EmailLog.objects.filter(
            user_id=user.pk,
            complained_at__isnull=False,
        ).exists()
        or SesEvent.objects.filter(
            user_id=user.pk,
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
        ).exists()
    )


def _email_context(event, recap_url):
    return {
        "event_title": event.title,
        "recap_url": recap_url,
        "event_url": f"{site_base_url().rstrip('/')}{event.get_absolute_url()}",
    }


def _save_email_success(event, user, email_log):
    """Attach the existing EmailLog and durable marker atomically."""
    with transaction.atomic():
        if email_log.event_id != event.pk:
            email_log.event_id = event.pk
            email_log.save(update_fields=["event"])
        marker, created = EventReminderLog.objects.get_or_create(
            event=event,
            user=user,
            interval=INTERVAL_EMAIL,
        )
    return marker, created


def _deliver_email(event, user_id, recap_url):
    user, skipped = _current_registered_user(event, user_id)
    if user is None:
        return DeliveryState(skipped)

    marker = EventReminderLog.objects.filter(
        event_id=event.pk,
        user_id=user.pk,
        interval=INTERVAL_EMAIL,
    ).first()
    if marker is not None:
        log = EmailLog.objects.filter(
            dedupe_key=_dedupe_key(event, user),
        ).first()
        return DeliveryState("already_sent", log.pk if log else None)

    if _user_has_complaint(user):
        return DeliveryState("skipped_complaint")

    if user_has_permanent_bounce(user):
        return DeliveryState("skipped_permanent_bounce")
    try:
        validate_email(user.email)
    except ValidationError:
        return DeliveryState("skipped_invalid_address")

    dedupe_key = _dedupe_key(event, user)
    existing_log = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
    if existing_log is not None:
        try:
            _save_email_success(event, user, existing_log)
        except IntegrityError:
            pass
        return DeliveryState("already_sent", existing_log.pk)

    try:
        email_log = EmailService().send(
            user,
            EMAIL_TYPE,
            _email_context(event, recap_url),
            dedupe_key=dedupe_key,
        )
        if email_log is None:
            return DeliveryState("skipped_email_policy")
        marker, created = _save_email_success(event, user, email_log)
    except IntegrityError:
        existing_log = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
        if existing_log is not None:
            return DeliveryState("already_sent", existing_log.pk)
        raise
    except Exception as exc:
        logger.warning(
            "event_recap_ready_email_failed event_id=%s user_id=%s error=%s",
            event.pk,
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        return DeliveryState("failed")
    return DeliveryState(
        "sent" if created else "already_sent",
        email_log.pk,
    )


def _deliver_in_app(event, user_id, recap_url):
    user, skipped = _current_registered_user(event, user_id)
    if user is None:
        return DeliveryState(skipped)

    marker = EventReminderLog.objects.filter(
        event_id=event.pk,
        user_id=user.pk,
        interval=INTERVAL_IN_APP,
    ).first()
    if marker is not None:
        notification = Notification.objects.filter(
            user_id=user.pk,
            notification_type=NOTIFICATION_TYPE,
            url=recap_url,
        ).order_by("-pk").first()
        return DeliveryState(
            "already_sent",
            notification.pk if notification else None,
        )

    try:
        with transaction.atomic():
            notification = Notification.objects.create(
                user=user,
                title=f"Recap ready: {event.title}",
                body=f"The recap for {event.title} is ready.",
                url=recap_url,
                notification_type=NOTIFICATION_TYPE,
            )
            marker = EventReminderLog.objects.create(
                event=event,
                user=user,
                interval=INTERVAL_IN_APP,
            )
    except IntegrityError:
        marker = EventReminderLog.objects.filter(
            event_id=event.pk,
            user_id=user.pk,
            interval=INTERVAL_IN_APP,
        ).first()
        if marker is not None:
            notification = Notification.objects.filter(
                user_id=user.pk,
                notification_type=NOTIFICATION_TYPE,
                url=recap_url,
            ).order_by("-pk").first()
            return DeliveryState(
                "already_sent",
                notification.pk if notification else None,
            )
        raise
    except Exception as exc:
        logger.warning(
            "event_recap_ready_in_app_failed event_id=%s user_id=%s error=%s",
            event.pk,
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        return DeliveryState("failed")
    return DeliveryState("sent", notification.pk)


def _recipient_result(event, user_id, recap_url):
    """Deliver each channel independently for one exact registrant."""
    result = {"user_id": user_id}
    try:
        email_state = _deliver_email(event, user_id, recap_url)
    except Exception as exc:  # noqa: BLE001 - one recipient cannot abort the fan-out
        logger.warning(
            "event_recap_ready_email_unhandled event_id=%s user_id=%s error=%s",
            event.pk,
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        email_state = DeliveryState("failed")
    result["email_status"] = email_state.status
    if email_state.identifier is not None:
        result["email_log_id"] = email_state.identifier

    try:
        in_app_state = _deliver_in_app(event, user_id, recap_url)
    except Exception as exc:  # noqa: BLE001 - continue with other recipients
        logger.warning(
            "event_recap_ready_in_app_unhandled event_id=%s user_id=%s error=%s",
            event.pk,
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        in_app_state = DeliveryState("failed")
    result["in_app_status"] = in_app_state.status
    if in_app_state.identifier is not None:
        result["notification_id"] = in_app_state.identifier
    return result


def _build_summary(event, recap_url, results, *, actor=None):
    sent_email = sum(item["email_status"] == "sent" for item in results)
    sent_in_app = sum(item["in_app_status"] == "sent" for item in results)
    already_email = sum(
        item["email_status"] == "already_sent" for item in results
    )
    already_in_app = sum(
        item["in_app_status"] == "already_sent" for item in results
    )
    already_sent = sum(
        item["email_status"] == "already_sent"
        and item["in_app_status"] == "already_sent"
        for item in results
    )
    failed = sum(
        item["email_status"] == "failed"
        or item["in_app_status"] == "failed"
        for item in results
    )
    skipped_inactive = sum(
        item["email_status"] == "skipped_inactive"
        or item["in_app_status"] == "skipped_inactive"
        for item in results
    )
    skipped = sum(
        item["email_status"].startswith("skipped_")
        or item["in_app_status"].startswith("skipped_")
        for item in results
    )
    summary = {
        "event": {
            "id": event.pk,
            "slug": event.slug,
            "title": event.title,
        },
        "recap_url": recap_url,
        "eligible": len(results),
        "emailed": sent_email,
        "notified": sent_in_app,
        "already_emailed": already_email,
        "already_notified": already_in_app,
        "already_sent": already_sent,
        "skipped_inactive": skipped_inactive,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }
    logger.info(
        "event_recap_ready_invocation actor_user_id=%s event_id=%s "
        "eligible=%s emailed=%s notified=%s already_sent=%s "
        "skipped_inactive=%s failed=%s",
        getattr(actor, "pk", None),
        event.pk,
        summary["eligible"],
        summary["emailed"],
        summary["notified"],
        summary["already_sent"],
        summary["skipped_inactive"],
        summary["failed"],
    )
    return summary


def notify_recap_ready(event, *, actor=None):
    """Explicitly deliver the recap-ready message to exact registrants.

    The event row is locked for the whole invocation. That keeps concurrent
    operators from crossing the external email boundary at the same time;
    the per-channel ``EventReminderLog`` rows remain the durable success
    markers used by retries and audit surfaces.
    """
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        recap_path = assert_recap_ready(locked_event)
        recap_url = absolute_recap_url(locked_event, recap_path)
        registrations = list(_active_registrations(locked_event))
        results = [
            _recipient_result(locked_event, registration.user_id, recap_url)
            for registration in registrations
        ]
        return _build_summary(
            locked_event,
            recap_url,
            results,
            actor=actor,
        )
