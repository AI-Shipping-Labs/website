"""Async outbound delivery task (issue #1070).

``deliver_webhook(emission_id, subscription_id)`` signs the envelope and
POSTs it to the subscription's handler URL, recording a ``WebhookDelivery``
row per attempt. Retries are handled by the ``async_task`` helper
(``max_retries``) — this task does NOT implement its own retry loop. On a
non-2xx response or a transport error it raises so Django-Q schedules the
retry; the per-attempt ``WebhookDelivery`` row is committed before the
raise so failures stay observable in the Studio log.
"""

import json
import logging
import time

import requests

from triggers.dispatch import build_envelope
from triggers.models import EventEmission, TriggerSubscription, WebhookDelivery
from triggers.signing import compute_signature

logger = logging.getLogger(__name__)

DELIVERY_TIMEOUT_SECONDS = 10
# Truncate stored response bodies so a chatty handler can't bloat the log.
RESPONSE_BODY_MAX_CHARS = 2000


def deliver_webhook(emission_id, subscription_id):
    """Sign and POST the envelope for one emission/subscription pair."""
    emission = EventEmission.objects.filter(pk=emission_id).first()
    subscription = TriggerSubscription.objects.filter(pk=subscription_id).first()
    if emission is None or subscription is None:
        logger.warning(
            "deliver_webhook: missing emission=%s or subscription=%s",
            emission_id,
            subscription_id,
        )
        return

    attempt = (
        WebhookDelivery.objects.filter(
            emission=emission, subscription=subscription,
        ).count()
        + 1
    )

    envelope = build_envelope(
        emission.event_name,
        emission.user,
        emission.properties,
        envelope_id=emission.envelope_id,
        min_level=emission.properties.get("min_level"),
    )
    raw_body = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-AISL-Signature": compute_signature(
            subscription.secret, timestamp, raw_body,
        ),
        "X-AISL-Timestamp": str(timestamp),
        "X-AISL-Event-Id": emission.envelope_id,
    }

    delivery = WebhookDelivery(
        emission=emission,
        subscription=subscription,
        target_url=subscription.target_url,
        request_body=raw_body,
        attempt=attempt,
    )

    try:
        response = requests.post(
            subscription.target_url,
            data=raw_body.encode("utf-8"),
            headers=headers,
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        delivery.succeeded = False
        delivery.error = str(exc)[:RESPONSE_BODY_MAX_CHARS]
        delivery.save()
        logger.warning(
            "Webhook delivery transport error for emission=%s sub=%s: %s",
            emission_id,
            subscription_id,
            exc,
        )
        raise

    delivery.response_status = response.status_code
    delivery.response_body = (response.text or "")[:RESPONSE_BODY_MAX_CHARS]
    delivery.succeeded = 200 <= response.status_code < 300
    delivery.save()

    if not delivery.succeeded:
        logger.warning(
            "Webhook delivery non-2xx (%s) for emission=%s sub=%s",
            response.status_code,
            emission_id,
            subscription_id,
        )
        raise RuntimeError(
            f"Handler returned {response.status_code} for "
            f"emission {emission_id}",
        )
