"""Durable, signed outbound delivery for custom event hooks."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from urllib3.exceptions import HTTPError

from jobs.tasks import async_task
from jobs.tasks.names import build_task_name
from triggers.destinations import post_pinned_https, validate_outbound_url
from triggers.dispatch import build_envelope
from triggers.models import (
    EventEmission,
    TriggerSubscription,
    WebhookDelivery,
    WebhookDeliveryJob,
)
from triggers.secrets import decrypt_secret
from triggers.signing import compute_signature

logger = logging.getLogger(__name__)

DELIVERY_TIMEOUT_SECONDS = 10
DELIVERY_LEASE_SECONDS = 90
RESPONSE_BODY_MAX_CHARS = 2000
RETRY_BASE_SECONDS = 30


def _ensure_job(emission, subscription):
    envelope = emission.envelope
    if not envelope:
        envelope = build_envelope(
            emission.event_name,
            emission.user,
            emission.properties,
            envelope_id=emission.envelope_id,
            min_level=emission.properties.get("min_level"),
            occurred_at=emission.occurred_at,
        )
        EventEmission.objects.filter(pk=emission.pk, envelope={}).update(envelope=envelope)
    raw_body = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    job, _ = WebhookDeliveryJob.objects.get_or_create(
        emission=emission,
        subscription=subscription,
        defaults={
            "target_url": subscription.target_url,
            "encrypted_secret": subscription.encrypted_secret,
            "secret_version": subscription.secret_version,
            "request_body": raw_body,
            "max_attempts": 4,
        },
    )
    return job


def _claim_attempt(job_id):
    """Lease one attempt, returning a detached job or ``None`` if not runnable."""
    now = timezone.now()
    with transaction.atomic():
        job = (
            WebhookDeliveryJob.objects.select_for_update()
            .select_related("subscription", "emission")
            .get(pk=job_id)
        )
        if job.status in {job.STATUS_SUCCEEDED, job.STATUS_FAILED}:
            return None
        if not job.subscription.is_active:
            job.status = job.STATUS_PAUSED
            job.lease_expires_at = None
            job.save(update_fields=["status", "lease_expires_at", "updated_at"])
            return None
        if (
            job.status == job.STATUS_RUNNING
            and job.lease_expires_at
            and job.lease_expires_at > now
        ):
            return None
        if job.next_attempt_at > now:
            return None
        if job.attempt_count >= job.max_attempts:
            job.status = job.STATUS_FAILED
            job.save(update_fields=["status", "updated_at"])
            return None
        job.attempt_count += 1
        job.status = job.STATUS_RUNNING
        job.lease_expires_at = now + timedelta(seconds=DELIVERY_LEASE_SECONDS)
        job.save(
            update_fields=[
                "attempt_count",
                "status",
                "lease_expires_at",
                "updated_at",
            ],
        )
        return job


def _finish_attempt(job, *, succeeded, response=None, error=""):
    """Append the immutable attempt log and transition the durable job."""
    now = timezone.now()
    response_status = response.status_code if response is not None else None
    response_body = ((response.text or "")[:RESPONSE_BODY_MAX_CHARS] if response is not None else "")
    with transaction.atomic():
        locked = WebhookDeliveryJob.objects.select_for_update().get(pk=job.pk)
        if (
            locked.status != locked.STATUS_RUNNING
            or locked.attempt_count != job.attempt_count
        ):
            # This worker's lease expired and a newer attempt now owns the job.
            return
        try:
            with transaction.atomic():
                WebhookDelivery.objects.create(
                    job=locked,
                    emission=locked.emission,
                    subscription=locked.subscription,
                    target_url=locked.target_url,
                    request_body=locked.request_body,
                    response_status=response_status,
                    response_body=response_body,
                    attempt=job.attempt_count,
                    succeeded=succeeded,
                    error=error[:RESPONSE_BODY_MAX_CHARS],
                )
        except IntegrityError:
            # A stale worker lost its lease. The unique job/attempt constraint
            # preserves one observable row and the winner owns the transition.
            return
        locked.lease_expires_at = None
        locked.last_error = "" if succeeded else error[:RESPONSE_BODY_MAX_CHARS]
        if succeeded:
            locked.status = locked.STATUS_SUCCEEDED
        elif locked.attempt_count >= locked.max_attempts:
            locked.status = locked.STATUS_FAILED
        else:
            locked.status = locked.STATUS_PENDING
            locked.next_attempt_at = now + timedelta(
                seconds=RETRY_BASE_SECONDS * (2 ** (locked.attempt_count - 1)),
            )
        locked.save(
            update_fields=[
                "status",
                "lease_expires_at",
                "last_error",
                "next_attempt_at",
                "updated_at",
            ],
        )


def deliver_webhook(emission_id, subscription_id):
    """Run at most one DB-leased attempt for an emission/subscription pair."""
    emission = EventEmission.objects.filter(pk=emission_id).first()
    subscription = TriggerSubscription.objects.filter(pk=subscription_id).first()
    if emission is None or subscription is None:
        logger.warning(
            "deliver_webhook: missing emission=%s or subscription=%s",
            emission_id,
            subscription_id,
        )
        return
    job = _ensure_job(emission, subscription)
    job = _claim_attempt(job.pk)
    if job is None:
        return

    try:
        addresses = validate_outbound_url(job.target_url)
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-AISL-Signature": compute_signature(
                decrypt_secret(job.encrypted_secret),
                timestamp,
                job.request_body,
            ),
            "X-AISL-Timestamp": str(timestamp),
            "X-AISL-Event-Id": job.emission.envelope_id,
            "X-AISL-Secret-Version": str(job.secret_version),
        }
        response = post_pinned_https(
            job.target_url,
            pinned_ip=sorted(addresses, key=str)[0],
            body=job.request_body.encode("utf-8"),
            headers=headers,
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
    except (HTTPError, OSError, ValidationError) as exc:
        _finish_attempt(job, succeeded=False, error=str(exc))
        logger.warning("Webhook delivery failed for job=%s: %s", job.pk, exc)
        return

    succeeded = 200 <= response.status_code < 300
    error = "" if succeeded else f"Handler returned {response.status_code}"
    _finish_attempt(job, succeeded=succeeded, response=response, error=error)


def resume_due_webhook_deliveries(limit=200):
    """Wake durable pending/expired jobs; safe to run every minute."""
    now = timezone.now()
    jobs = WebhookDeliveryJob.objects.filter(
        status__in=[
            WebhookDeliveryJob.STATUS_PENDING,
            WebhookDeliveryJob.STATUS_RUNNING,
            WebhookDeliveryJob.STATUS_PAUSED,
        ],
        subscription__is_active=True,
        attempt_count__lt=models.F("max_attempts"),
    ).filter(
        models.Q(status=WebhookDeliveryJob.STATUS_PENDING, next_attempt_at__lte=now)
        | models.Q(status=WebhookDeliveryJob.STATUS_RUNNING, lease_expires_at__lte=now)
        | models.Q(status=WebhookDeliveryJob.STATUS_PAUSED)
    )[:limit]
    count = 0
    for job in jobs:
        async_task(
            "triggers.tasks.deliver_webhook",
            job.emission_id,
            job.subscription_id,
            max_retries=0,
            task_name=build_task_name(
                "Resume webhook",
                f"job {job.pk}",
                f"subscription {job.subscription_id}",
            ),
        )
        count += 1
    return {"enqueued": count}
