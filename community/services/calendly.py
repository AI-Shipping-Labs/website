"""Calendly webhook ingestion + capacity bookkeeping (issue #884, Phase 2).

Captures booked calls from the Calendly ``invitee.created`` /
``invitee.canceled`` webhooks into :class:`community.models.BookedCall`,
matches the invitee to a member by email, and keeps the host's
``current_load`` accurate so ``/request-a-call`` availability reflects
reality without manual staff edits.

Design guarantees (acceptance criteria on #884):

- Signature-verified: when validation is enabled and the signing key is
  set, only requests carrying a valid ``Calendly-Webhook-Signature``
  header are processed.
- Best-effort + non-corrupting: capacity is only ever changed inside the
  same DB transaction that records or cancels the booking, and the
  Calendly event URI is a unique idempotency key, so a webhook
  re-delivery never double-counts. A processing failure cannot leave a
  ``BookedCall`` row out of sync with ``current_load``.
"""

import hashlib
import hmac
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from community.calendly_config import (
    calendly_webhook_validation_enabled,
    get_calendly_webhook_signing_key,
)
from community.models import STATUS_BOOKED, STATUS_CANCELED, BookedCall, CallHost

logger = logging.getLogger(__name__)

User = get_user_model()

EVENT_INVITEE_CREATED = 'invitee.created'
EVENT_INVITEE_CANCELED = 'invitee.canceled'


def verify_signature(request):
    """Verify the ``Calendly-Webhook-Signature`` header.

    Calendly signs webhooks with HMAC-SHA256 over ``"{t}.{body}"`` and
    sends ``Calendly-Webhook-Signature: t=<timestamp>,v1=<hex>``.

    Returns True when the signature is valid. When validation is
    disabled (the Studio toggle is off) this returns True so local
    replay works without a signing key. When validation is enabled but
    the key or header is missing/invalid, returns False.
    """
    if not calendly_webhook_validation_enabled():
        return True

    signing_key = get_calendly_webhook_signing_key()
    if not signing_key:
        logger.warning('Calendly webhook validation enabled but signing key not set')
        return False

    header = request.headers.get('Calendly-Webhook-Signature', '')
    timestamp, signature = _parse_signature_header(header)
    if not timestamp or not signature:
        return False

    body = request.body.decode('utf-8')
    signed_payload = f'{timestamp}.{body}'
    expected = hmac.new(
        signing_key.encode('utf-8'),
        signed_payload.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _parse_signature_header(header):
    """Pull ``t`` and ``v1`` out of the Calendly signature header."""
    timestamp = ''
    signature = ''
    for part in header.split(','):
        key, _, value = part.strip().partition('=')
        if key == 't':
            timestamp = value
        elif key == 'v1':
            signature = value
    return timestamp, signature


def _match_host(payload_resource):
    """Find the CallHost this booking belongs to.

    Calendly puts the event-type owner on the resource. We match on the
    scheduling URL stored in ``CallHost.booking_url`` (the host's
    Calendly link). When no host matches we still record the call but
    cannot adjust a specific host's load — so we skip silently and log.
    """
    scheduling_url = (payload_resource.get('scheduling_url') or '').strip()
    if scheduling_url:
        host = CallHost.objects.filter(booking_url=scheduling_url).first()
        if host is not None:
            return host
    # Fall back to event-type membership URI prefix matching.
    return None


def _extract_invitee(payload_resource):
    """Return (email, name, invitee_uri) from the webhook resource."""
    email = (payload_resource.get('email') or '').strip().lower()
    name = (payload_resource.get('name') or '').strip()
    invitee_uri = (payload_resource.get('uri') or '').strip()
    return email, name, invitee_uri


def _extract_event(payload_resource):
    """Return (event_uri, scheduled_at) from the webhook resource.

    The invitee payload nests the scheduled event under
    ``scheduled_event`` with the event ``uri`` and ``start_time``.
    """
    scheduled_event = payload_resource.get('scheduled_event') or {}
    event_uri = (scheduled_event.get('uri') or '').strip()
    start_time = scheduled_event.get('start_time')
    scheduled_at = parse_datetime(start_time) if start_time else None
    return event_uri, scheduled_at


@transaction.atomic
def handle_invitee_created(payload):
    """Record a booked call and consume one slot of host capacity.

    Idempotent on the Calendly event URI: a re-delivered webhook updates
    the existing row and does NOT increment ``current_load`` again.
    Returns the BookedCall, or None when the payload lacked an event URI.
    """
    resource = payload.get('payload') or {}
    event_uri, scheduled_at = _extract_event(resource)
    if not event_uri:
        logger.warning('Calendly invitee.created webhook missing event URI')
        return None

    email, name, invitee_uri = _extract_invitee(resource)
    host = _match_host(resource)
    member = User.objects.filter(email__iexact=email).first() if email else None

    # Lock the row (if any) to make the create-or-update idempotent under
    # concurrent re-deliveries.
    existing = (
        BookedCall.objects
        .select_for_update()
        .filter(calendly_event_uri=event_uri)
        .first()
    )

    if existing is not None:
        # Re-delivery (or a previously canceled booking re-created). Only
        # bump capacity when transitioning back into the booked state.
        was_active = existing.is_active
        existing.invitee_email = email or existing.invitee_email
        existing.invitee_name = name or existing.invitee_name
        existing.scheduled_at = scheduled_at or existing.scheduled_at
        existing.calendly_invitee_uri = invitee_uri or existing.calendly_invitee_uri
        existing.member = member or existing.member
        existing.status = STATUS_BOOKED
        existing.canceled_at = None
        existing.save()
        if host is not None and not was_active:
            _increment_load(host)
        return existing

    booked = BookedCall.objects.create(
        host=host,
        member=member,
        invitee_email=email,
        invitee_name=name,
        scheduled_at=scheduled_at,
        status=STATUS_BOOKED,
        calendly_event_uri=event_uri,
        calendly_invitee_uri=invitee_uri,
        reschedule_url=(resource.get('reschedule_url') or '')[:500],
        cancel_url=(resource.get('cancel_url') or '')[:500],
    )
    if host is not None:
        _increment_load(host)
    return booked


@transaction.atomic
def handle_invitee_canceled(payload):
    """Mark the matching booked call canceled and free one host slot.

    Idempotent: canceling an already-canceled (or unknown) booking does
    not decrement capacity. Returns the BookedCall, or None when no
    matching booking is found.
    """
    resource = payload.get('payload') or {}
    event_uri, _ = _extract_event(resource)
    _, _, invitee_uri = _extract_invitee(resource)

    qs = BookedCall.objects.select_for_update()
    booked = None
    if event_uri:
        booked = qs.filter(calendly_event_uri=event_uri).first()
    if booked is None and invitee_uri:
        booked = qs.filter(calendly_invitee_uri=invitee_uri).first()
    if booked is None:
        logger.info('Calendly invitee.canceled for unknown booking; ignoring')
        return None

    if not booked.is_active:
        return booked

    booked.status = STATUS_CANCELED
    booked.canceled_at = timezone.now()
    booked.save(update_fields=['status', 'canceled_at', 'updated_at'])
    if booked.host_id is not None:
        _decrement_load(booked.host)
    return booked


def _increment_load(host):
    """Atomically bump the host's current_load by one."""
    CallHost.objects.filter(pk=host.pk).update(current_load=F('current_load') + 1)


def _decrement_load(host):
    """Atomically drop the host's current_load by one, never below zero."""
    (
        CallHost.objects
        .filter(pk=host.pk, current_load__gt=0)
        .update(current_load=F('current_load') - 1)
    )


def process_webhook(payload):
    """Dispatch a parsed Calendly webhook payload to its handler.

    Returns the affected BookedCall or None. Unknown event types are
    ignored (returns None). Raises nothing for routing — callers wrap
    the call so a handler error returns 200 to Calendly.
    """
    event = payload.get('event', '')
    if event == EVENT_INVITEE_CREATED:
        return handle_invitee_created(payload)
    if event == EVENT_INVITEE_CANCELED:
        return handle_invitee_canceled(payload)
    logger.info('Ignoring unhandled Calendly event type: %s', event)
    return None
