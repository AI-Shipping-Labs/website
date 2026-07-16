"""The single ``emit_event`` entrypoint for the event-hooks subsystem.

``emit_event(name, user, properties)`` is the only way the rest of the
codebase triggers an outbound webhook. It:

1. Short-circuits when ``TRIGGERS_ENABLED`` is off (records nothing,
   dispatches nothing).
2. Builds the envelope and records an ``EventEmission`` — the unique
   ``(user, event_name)`` constraint enforces dedup, so a duplicate claim
   returns the EXISTING emission and does NOT re-dispatch.
3. Finds active ``TriggerSubscription``s of ``event_type='custom'`` whose
   exact-match ``property_filter`` matches the envelope properties.
4. Enqueues a signed async POST per matched subscription via the shared
   ``jobs.tasks.async_task`` helper, so a slow/dead handler never blocks
   the claim.

The Lambda fulfilment (code pool + SES send) is out of scope here; this
module only signs and dispatches the envelope.
"""

import json
import logging
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from integrations.config import is_enabled
from jobs.tasks import async_task
from jobs.tasks.names import build_task_name
from triggers.models import (
    EVENT_TYPE_CUSTOM,
    EventEmission,
    TriggerSubscription,
    WebhookDeliveryJob,
)
from website.release_phase import background_work_enabled

logger = logging.getLogger(__name__)

# Retry the outbound POST a few times via the async_task helper rather than
# a bespoke loop, so a transient handler error is retried with backoff.
DELIVERY_MAX_RETRIES = 3


def build_envelope(
    name,
    user,
    properties,
    *,
    envelope_id,
    min_level=None,
    occurred_at=None,
):
    """Build the wire envelope for an emitted event.

    Kept pure (no DB writes) so tests and the signer can reuse it.
    """
    data = {
        "user_id": user.id if user is not None else None,
        "email": getattr(user, "email", None) if user is not None else None,
        "name": _display_name(user),
        "min_level": min_level,
        "properties": properties or {},
    }
    return {
        "event": name,
        "id": envelope_id,
        "occurred_at": (occurred_at or timezone.now()).isoformat(),
        "data": data,
    }


def _display_name(user):
    if user is None:
        return None
    for attr in ("get_full_name", "name"):
        value = getattr(user, attr, None)
        if callable(value):
            value = value()
        if value:
            return value
    return getattr(user, "email", None)


def emit_event(name, user, properties=None, *, min_level=None):
    """Record an emission and dispatch matching subscriptions.

    Returns a ``(emission, created)`` tuple:

    - ``created=True`` — a new emission was recorded and matched
      subscriptions were enqueued.
    - ``created=False`` — either the flag is off (``emission`` is None) or
      the ``(user, event_name)`` pair already exists (dedup; the existing
      emission is returned and NOTHING is re-dispatched).
    """
    properties = dict(properties or {})

    # Persist ``min_level`` into the emission properties so the wire
    # envelope's ``data.min_level`` carries the real value. ``deliver_webhook``
    # reads it back from ``emission.properties`` (the emission is the only
    # state the async task sees), so dropping it here would send
    # ``data.min_level: null``. An explicit ``min_level`` already in
    # ``properties`` wins over the keyword.
    if min_level is not None and "min_level" not in properties:
        properties["min_level"] = min_level

    if not is_enabled("TRIGGERS_ENABLED"):
        logger.info("TRIGGERS_ENABLED is off; skipping emit for %s", name)
        return None, False

    envelope_id = f"evt_{uuid.uuid4().hex}"
    occurred_at = timezone.now()
    authenticated_user = user if (user is not None and user.is_authenticated) else None
    envelope = build_envelope(
        name,
        authenticated_user,
        properties,
        envelope_id=envelope_id,
        min_level=properties.get("min_level"),
        occurred_at=occurred_at,
    )

    try:
        with transaction.atomic():
            emission = EventEmission.objects.create(
                user=authenticated_user,
                event_name=name,
                properties=properties,
                envelope_id=envelope_id,
                occurred_at=occurred_at,
                envelope=envelope,
            )
    except IntegrityError:
        # Duplicate (user, event_name): one-shot dedup. Return the existing
        # emission and do NOT re-dispatch.
        existing = EventEmission.objects.filter(
            user=user, event_name=name,
        ).first()
        logger.info("Duplicate emit for %s by user %s; no re-dispatch", name, user)
        return existing, False

    _dispatch_to_subscriptions(emission, name, properties)
    return emission, True


def _dispatch_to_subscriptions(emission, name, properties):
    """Enqueue a signed delivery for every matching active subscription."""
    if not background_work_enabled():
        # R1 preserves the exact queue vocabulary understood by 524153b6.
        # Durable job creation/recovery is activated only in R2, but webhook
        # delivery itself must not be dropped during the compatibility soak.
        subscriptions = TriggerSubscription.objects.filter(
            is_active=True,
            event_type=EVENT_TYPE_CUSTOM,
        )
        for subscription in subscriptions:
            if not subscription.matches(properties):
                continue
            async_task(
                "triggers.tasks.deliver_webhook",
                emission.id,
                subscription.id,
                max_retries=DELIVERY_MAX_RETRIES,
                task_name=build_task_name(
                    "Deliver webhook",
                    name,
                    f"subscription {subscription.id}",
                ),
            )
        return
    subscriptions = TriggerSubscription.objects.filter(
        is_active=True,
        event_type=EVENT_TYPE_CUSTOM,
    )
    for subscription in subscriptions:
        if not subscription.matches(properties):
            continue
        raw_body = json.dumps(
            emission.envelope,
            separators=(",", ":"),
            sort_keys=True,
        )
        WebhookDeliveryJob.objects.get_or_create(
            emission=emission,
            subscription=subscription,
            defaults={
                "target_url": subscription.target_url,
                "encrypted_secret": subscription.encrypted_secret,
                "secret_version": subscription.secret_version,
                "request_body": raw_body,
                "max_attempts": DELIVERY_MAX_RETRIES + 1,
            },
        )
        try:
            async_task(
                "triggers.tasks.deliver_webhook",
                emission.id,
                subscription.id,
                # Retry ownership lives in WebhookDeliveryJob. django-q only
                # wakes the durable state machine and must add no attempts.
                max_retries=0,
                task_name=build_task_name(
                    "Deliver webhook",
                    name,
                    f"subscription {subscription.id}",
                ),
            )
        except Exception:
            # The durable pending row is recoverable by the minute schedule;
            # queue availability must never turn a valid claim into a 500.
            logger.exception(
                "Initial webhook enqueue failed for emission=%s subscription=%s",
                emission.pk,
                subscription.pk,
            )
