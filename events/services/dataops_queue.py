"""Call back into the DataOps ad-hoc queue when an event changes.

DataOps keeps Cards and campaign dates anchored on AISL events, so it must
learn when an event is created, moved, renamed, relinked or cancelled.
``post_save`` enqueues one delivery per change on the django-q worker after
the transaction commits; the delivery retries with backoff and, when every
attempt fails, alerts the staff Slack channel and the dev mailbox. Nothing
here may raise into the save path: a DataOps outage never blocks an edit.

Configuration: ``DATAOPS_QUEUE_URL`` (``https://<dataops>/api/v1/queue/items``)
and ``DATAOPS_QUEUE_TOKEN`` (the ``adhoc-queue.aisl`` secret in DataOps).
"""

import hashlib
import json
import logging
import time

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

logger = logging.getLogger(__name__)

SOURCE = "aisl"
TRACKED_FIELDS = ("title", "start_datetime", "end_datetime", "status", "zoom_join_url", "location", "slug")
ALERT_RECIPIENT = "dev@dtcdev.click"
RETRY_DELAYS_SECONDS = (2, 5, 15, 30)
REQUEST_TIMEOUT_SECONDS = 10


def _value(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def snapshot(event):
    return {field: _value(getattr(event, field, None)) for field in TRACKED_FIELDS}


def build_item(event, before, created):
    """Return the queue item for this save, or None when nothing tracked changed."""
    after = snapshot(event)
    if created:
        kind = "event.created"
        changes = {}
    else:
        if before is None:
            return None
        changes = {f: [before[f], after[f]] for f in TRACKED_FIELDS if before[f] != after[f]}
        if not changes:
            return None
        kind = "event.cancelled" if "status" in changes and after["status"] == "cancelled" else "event.updated"
    base = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
    links = []
    if base and event.pk:
        try:
            links.append({"label": "Event page", "url": f"{base}{event.get_absolute_url()}"})
        except Exception:  # noqa: BLE001 - a link is a nicety, never a blocker
            pass
        links.append({"label": "Studio", "url": f"{base}/studio/events/{event.pk}/edit"})
    when = after["start_datetime"] or "no date"
    verb = {"event.created": "created", "event.cancelled": "cancelled", "event.updated": "updated"}[kind]
    digest = hashlib.sha256(json.dumps([kind, after, changes], sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {
        "source": SOURCE,
        "kind": kind,
        "title": f"AISL event {verb}: {event.title} ({when})"[:300],
        "payload": {"eventId": event.pk, "event": after, "changes": changes},
        "links": links,
        "idempotencyKey": f"event-{event.pk}-{kind}-{digest}",
    }


def is_configured():
    return bool(getattr(settings, "DATAOPS_QUEUE_URL", "") and getattr(settings, "DATAOPS_QUEUE_TOKEN", ""))


def on_event_saved(event, before, created):
    """Signal body: enqueue after commit; never raise into the save."""
    if not is_configured():
        # Local dev and tests run without DataOps; prod and dev set both values.
        logger.debug("DataOps queue callback not configured; skipping event %s", getattr(event, "pk", None))
        return
    try:
        item = build_item(event, before, created)
        if item is None:
            return
        transaction.on_commit(lambda: enqueue_delivery(item))
    except Exception:  # noqa: BLE001 - the save must succeed regardless
        logger.exception("DataOps queue callback could not be prepared for event %s", getattr(event, "pk", None))


def enqueue_delivery(item):
    try:
        from jobs.tasks import async_task  # noqa: PLC0415 - jobs imports events

        async_task("events.services.dataops_queue.deliver", item, max_retries=0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("DataOps queue callback could not be enqueued: %s", item["idempotencyKey"])
        alert_failure(item, f"enqueue failed: {type(exc).__name__}: {exc}")


def _post(item):
    url = getattr(settings, "DATAOPS_QUEUE_URL", "")
    token = getattr(settings, "DATAOPS_QUEUE_TOKEN", "")
    if not url or not token:
        raise RuntimeError("DATAOPS_QUEUE_URL / DATAOPS_QUEUE_TOKEN are not configured")
    response = requests.post(
        url,
        json=item,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"DataOps queue answered {response.status_code}: {response.text[:300]}")
    return response


def deliver(item, sleep=time.sleep):
    """Worker body: post with retries; alert loudly when every attempt fails."""
    last_error = None
    for attempt, delay in enumerate((0, *RETRY_DELAYS_SECONDS)):
        if delay:
            sleep(delay)
        try:
            _post(item)
            return True
        except Exception as exc:  # noqa: BLE001 - retried, then alerted
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("DataOps queue delivery attempt %s failed for %s: %s", attempt + 1, item["idempotencyKey"], last_error)
            if "answered 4" in last_error and "answered 429" not in last_error:
                break  # a 4xx will not get better by retrying
    logger.error("DataOps queue delivery failed for %s: %s", item["idempotencyKey"], last_error)
    alert_failure(item, last_error)
    raise RuntimeError(f"DataOps queue delivery failed: {last_error}")


def alert_failure(item, error):
    subject = f"AISL -> DataOps callback failed: {item.get('kind')} event {item.get('payload', {}).get('eventId')}"
    body = (
        f"{item.get('title')}\nidempotencyKey: {item.get('idempotencyKey')}\nerror: {error}\n"
        "DataOps did not learn about this event change. Fix the cause and re-save the event, "
        "or post the item to DataOps manually."
    )
    delivered = False
    try:
        from community.slack_config import slack_api_enabled  # noqa: PLC0415
        from notifications.services.staff_slack import (  # noqa: PLC0415
            escape_slack_text,
            message_payload,
            mrkdwn,
            post_staff_slack_message,
            staff_alert_channel_id,
        )

        channel_id = staff_alert_channel_id() if slack_api_enabled() else None
        if channel_id:
            text = f"*{escape_slack_text(subject)}*\n{escape_slack_text(body)}"
            delivered = post_staff_slack_message(channel_id, message_payload(text, [{"type": "section", "text": mrkdwn(text)}])).ok
    except Exception:  # noqa: BLE001
        logger.exception("Slack alert for DataOps callback failed")
    try:
        delivered = bool(send_mail(subject, body, None, [ALERT_RECIPIENT])) or delivered
    except Exception:  # noqa: BLE001
        logger.exception("Email alert for DataOps callback failed")
    if not delivered:
        logger.critical("Every alert route failed for DataOps callback %s", item.get("idempotencyKey"))
    return delivered
