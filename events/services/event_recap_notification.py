"""Explicit recap-ready delivery for everyone interested in an event.

Issue #1557 introduced the registrant-only notice. The audience now comes
from the shared resolver in ``events.services.event_audience`` (also used by
the pre-event reminders): the event's registrants/attendees, the members of
every dated cohort whose ``event_series`` is the event's series, and the
readers of a book club linked to the event, deduplicated per user with the
reasons each recipient was included for (reported by the dry-run preview).

The notice goes out automatically once, when a recap becomes ready (see
``maybe_enqueue_recap_auto_notify``), and stays available as a manual
command/API/Studio action. The per-user ``EventReminderLog`` markers make
every path idempotent.

The email channel goes through ``community_base.mail`` (A1.2 slice 3): a
durable ``EmailDelivery`` under the ``event-recap-ready:{event}:{user}``
idempotency key, transported by the worker. ``sent`` therefore means the
durable delivery exists; the provider outcome lands on the delivery and
the ``EmailLog`` audit row is written from the delivery worker, keeping
its event FK via the delivery's ``related`` relation. The in-app channel
is unchanged.
"""

import logging
from dataclasses import dataclass

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from email_app.models import EmailLog
from email_app.package_mail import send_package_mail
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES
from events.services.event_audience import email_skip_status, resolve_event_audience
from integrations.config import recap_auto_notify_enabled, site_base_url
from notifications.models import EventReminderLog, Notification

logger = logging.getLogger(__name__)
User = get_user_model()

EMAIL_TYPE = "event_recap_ready"
NOTIFICATION_TYPE = "event_recap"
INTERVAL_EMAIL = "recap_email"
INTERVAL_IN_APP = "recap_in_app"
# Per-event guard (``user`` NULL) written when the automatic notice runs, so
# the recap is announced automatically at most once per event.
INTERVAL_AUTO_GUARD = "recap_auto"


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
    """Return the relative canonical recap URL once the exact audience can open it.

    This mirrors the public recap view's derived state. There is deliberately
    no stored "announced" or "published recap" flag: recap publication remains
    the combination of content, event timing, public event state, and the
    existing canonical route.

    Issue #1660: for a hidden-series event, "readiness" no longer means an
    anonymous visitor could open the recap page — a hidden series' recap
    page 404s for anyone not staff or entitled. It means the exact
    registrant audience (who registered via the cohort/sprint enrollment
    flow and are therefore entitled by construction) can open it. No
    additional gate is needed here: the checks below remain the correct and
    sufficient readiness signal for both public and hidden series.
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


def is_recap_ready(event):
    """Boolean form of :func:`assert_recap_ready`."""
    try:
        assert_recap_ready(event)
    except EventRecapNotReady:
        return False
    return True


def _recap_already_announced(event_id):
    """True once the automatic notice ran or any recap email was recorded."""
    return EventReminderLog.objects.filter(
        event_id=event_id,
        interval__in=(INTERVAL_AUTO_GUARD, INTERVAL_EMAIL),
    ).exists()


def maybe_enqueue_recap_auto_notify(event, *, was_ready):
    """Queue the automatic recap notice when the recap has just become ready.

    Called after an event save (Studio, API, the recap draft, sync) with
    ``was_ready`` describing the stored row before the save, and by the
    completion cron for events that just ended. Only a not-ready -> ready
    transition queues work, so editing an already-announced recap never
    resends; an event whose recap was already announced (automatic guard
    or any recap email marker, e.g. a manual send) is skipped too. The job
    runs on django-q after the surrounding transaction commits.
    """
    if was_ready or not recap_auto_notify_enabled():
        return False
    if not is_recap_ready(event):
        return False
    if _recap_already_announced(event.pk):
        return False
    event_id = event.pk
    transaction.on_commit(lambda: enqueue_recap_auto_notify(event_id))
    return True


def enqueue_recap_auto_notify(event_id):
    """Enqueue the automatic recap notice on the django-q worker."""
    from jobs.tasks import async_task, build_task_name  # noqa: PLC0415 - jobs imports events

    return async_task(
        "events.tasks.notify_recap_auto.send_recap_auto_notify",
        event_id,
        task_name=build_task_name(
            "Send recap-ready notice",
            f"event #{event_id}",
            "recap became ready",
        ),
    )


def send_recap_auto_notify(event_id):
    """Worker body: announce the recap once, guarded per event.

    The guard row is written under the event row lock before any send, so
    two queued jobs for the same event (two quick saves) announce once. The
    per-user markers inside :func:`notify_recap_ready` stay the recipient
    level idempotency gate.
    """
    if not recap_auto_notify_enabled():
        return {"status": "skipped", "reason": "disabled", "event_id": event_id}
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            return {"status": "skipped", "reason": "missing_event", "event_id": event_id}
        if not is_recap_ready(event):
            return {"status": "skipped", "reason": "not_ready", "event_id": event_id}
        if EventReminderLog.objects.filter(
            event_id=event_id, user__isnull=True, interval=INTERVAL_AUTO_GUARD,
        ).exists():
            return {"status": "skipped", "reason": "already_announced", "event_id": event_id}
        EventReminderLog.objects.create(
            event=event, user=None, interval=INTERVAL_AUTO_GUARD,
        )
    summary = notify_recap_ready(event)
    return {
        "status": "sent",
        "event_id": event_id,
        "eligible": summary["eligible"],
        "emailed": summary["emailed"],
        "notified": summary["notified"],
        "skipped": summary["skipped"],
        "failed": summary["failed"],
    }


def absolute_recap_url(event, recap_path=None):
    """Return the canonical absolute recap URL used by both channels."""
    recap_path = recap_path or event.get_recap_url()
    if not recap_path:
        return ""
    return f"{site_base_url().rstrip('/')}{recap_path}"


def resolve_recap_audience(event):
    """Return the recap audience: registrants, cohort members, book readers."""
    return resolve_event_audience(event)


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

    user_ids = [member.user.pk for member in resolve_recap_audience(event)]
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


def _current_active_user(user_id):
    """Re-check account activity immediately before each channel's delivery."""
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None, "skipped_inactive"
    return user, ""


def _dedupe_key(event, user):
    return f"event-recap-ready:{event.pk}:{user.pk}"


def _save_email_success(event, user, email_log=None):
    """Attach the legacy EmailLog (when present) and durable marker atomically.

    ``email_log`` is only set on the pre-adoption repair path, where an
    ``EmailLog`` row from the synchronous era is adopted into this event's
    marker set. Package-path sends pass no log: the audit row is written
    from the delivery worker with the event FK from the delivery's
    ``related`` relation.
    """
    with transaction.atomic():
        if email_log is not None and email_log.event_id != event.pk:
            email_log.event_id = event.pk
            email_log.save(update_fields=["event"])
        marker, created = EventReminderLog.objects.get_or_create(
            event=event,
            user=user,
            interval=INTERVAL_EMAIL,
        )
    return marker, created


def _email_already_sent(event, user_id):
    return EventReminderLog.objects.filter(
        event_id=event.pk,
        user_id=user_id,
        interval=INTERVAL_EMAIL,
    ).exists()


def _deliver_email(event, user_id, signed_up):
    user, skipped = _current_active_user(user_id)
    if user is None:
        return DeliveryState(skipped)

    if _email_already_sent(event, user.pk):
        log = EmailLog.objects.filter(
            dedupe_key=_dedupe_key(event, user),
        ).first()
        return DeliveryState("already_sent", log.pk if log else None)

    skipped = email_skip_status(user, signed_up)
    if skipped:
        return DeliveryState(skipped)

    dedupe_key = _dedupe_key(event, user)
    existing_log = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
    if existing_log is not None:
        # Pre-adoption row from the synchronous send era: adopt it
        # into the marker set instead of sending a second recap email.
        try:
            _save_email_success(event, user, existing_log)
        except IntegrityError:
            pass
        return DeliveryState("already_sent", existing_log.pk)

    try:
        delivery = send_package_mail(
            user,
            EMAIL_TYPE,
            # Issue #1613: the worker rebuilds title and every link from
            # the saved event; the durable context stays empty.
            {},
            idempotency_key=dedupe_key,
            related=event,
        )
    except Exception as exc:
        logger.warning(
            "event_recap_ready_email_failed event_id=%s user_id=%s error=%s",
            event.pk,
            user_id,
            exc.__class__.__name__,
            exc_info=True,
        )
        return DeliveryState("failed")

    if delivery.state == EmailDelivery.State.SUPPRESSED:
        return DeliveryState("skipped_email_policy")

    try:
        marker, created = _save_email_success(event, user)
    except IntegrityError:
        # A concurrent worker created the marker first; the durable
        # delivery is shared, so this is the same send.
        return DeliveryState("already_sent", delivery.pk)
    return DeliveryState(
        "sent" if created else "already_sent",
        delivery.pk,
    )


def _deliver_in_app(event, user_id, recap_url):
    user, skipped = _current_active_user(user_id)
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


def _recipient_result(event, member, recap_url):
    """Deliver each channel independently for one audience member."""
    user_id = member.user.pk
    result = {
        "user_id": user_id,
        "reasons": [reason.as_dict() for reason in member.reasons],
    }
    try:
        email_state = _deliver_email(event, user_id, member.signed_up)
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
        "dry_run": False,
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
        "by_reason": _reason_counts(item["reasons"] for item in results),
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
    """Explicitly deliver the recap-ready message to the resolved audience.

    The event row is locked for the whole invocation. That keeps concurrent
    operators from crossing the external email boundary at the same time;
    the per-channel ``EventReminderLog`` rows remain the durable success
    markers used by retries and audit surfaces.
    """
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        recap_path = assert_recap_ready(locked_event)
        recap_url = absolute_recap_url(locked_event, recap_path)
        results = [
            _recipient_result(locked_event, member, recap_url)
            for member in resolve_recap_audience(locked_event)
        ]
        return _build_summary(
            locked_event,
            recap_url,
            results,
            actor=actor,
        )


def _reason_counts(reason_lists):
    """Count recipients per reason source (a user can count in several)."""
    counts = {}
    for reasons in reason_lists:
        for source in {reason["source"] for reason in reasons}:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _preview_email_status(event, member):
    if _email_already_sent(event, member.user.pk):
        return "already_sent"
    return email_skip_status(member.user, member.signed_up) or "would_send"


def _preview_in_app_status(event, member):
    already = EventReminderLog.objects.filter(
        event_id=event.pk,
        user_id=member.user.pk,
        interval=INTERVAL_IN_APP,
    ).exists()
    return "already_sent" if already else "would_notify"


def preview_recap_audience(event):
    """Dry run: list the audience with reasons and predicted statuses.

    Sends nothing and writes nothing. Unlike ``notify_recap_ready`` it does
    not raise when the recap is not ready yet; ``ready``/``reason_code``
    report the guard so an operator can review the audience beforehand.
    """
    try:
        recap_path = assert_recap_ready(event)
        ready, reason, reason_code = True, "", ""
    except EventRecapNotReady as exc:
        recap_path = None
        ready, reason, reason_code = False, str(exc), exc.reason

    recipients = []
    for member in resolve_recap_audience(event):
        recipients.append({
            "user_id": member.user.pk,
            "email": member.user.email,
            "name": member.user.get_full_name(),
            "reasons": [item.as_dict() for item in member.reasons],
            "email_status": _preview_email_status(event, member),
            "in_app_status": _preview_in_app_status(event, member),
        })
    email_statuses = [item["email_status"] for item in recipients]
    return {
        "dry_run": True,
        "event": {"id": event.pk, "slug": event.slug, "title": event.title},
        "ready": ready,
        "reason": reason,
        "reason_code": reason_code,
        "recap_url": absolute_recap_url(event, recap_path),
        "eligible": len(recipients),
        "would_email": email_statuses.count("would_send"),
        "already_emailed": email_statuses.count("already_sent"),
        "skipped": sum(status.startswith("skipped_") for status in email_statuses),
        "by_reason": _reason_counts(item["reasons"] for item in recipients),
        "results": recipients,
    }
