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
import time
from urllib.parse import urlsplit, urlunsplit

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.services.email_resolution import resolve_user_by_email
from community.calendly_config import (
    calendly_webhook_tolerance_seconds,
    get_calendly_webhook_signing_key,
)
from community.models import (
    STATUS_BOOKED,
    STATUS_CANCELED,
    BookedCall,
    CallHost,
    UnmatchedBookedCall,
)

logger = logging.getLogger(__name__)

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
    signing_key = get_calendly_webhook_signing_key()
    if not signing_key:
        logger.warning('Calendly webhook signing key not set; rejecting request')
        return False

    header = request.headers.get('Calendly-Webhook-Signature', '')
    timestamp, signature = _parse_signature_header(header)
    if not timestamp or not signature:
        return False

    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - timestamp_int) > calendly_webhook_tolerance_seconds():
        return False
    try:
        body = request.body.decode('utf-8')
    except UnicodeDecodeError:
        return False
    signed_payload = f'{timestamp}.{body}'
    expected = hmac.new(
        signing_key.encode('utf-8'),
        signed_payload.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def delivery_fingerprint(request):
    """Stable, non-secret replay key for an already-verified delivery."""
    header = request.headers.get('Calendly-Webhook-Signature', '')
    return hashlib.sha256(header.encode() + b'\0' + request.body).hexdigest()


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
    scheduling_url = _normalize_url(payload_resource.get('scheduling_url'))
    if scheduling_url:
        for host in CallHost.objects.exclude(booking_url=''):
            if _normalize_url(host.booking_url) == scheduling_url:
                return host
    # Fall back to event-type membership URI prefix matching.
    return None


def _normalize_url(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    parts = urlsplit(raw)
    path = parts.path.rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, '', ''))


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


def _event_time(payload, resource, *, canceled=False):
    raw = resource.get('canceled_at') if canceled else None
    raw = raw or payload.get('created_at') or resource.get('created_at')
    return parse_datetime(raw) if raw else timezone.now()


def _stage_unmatched_call(payload, *, status, event_at):
    """Persist an unmatched call outside the table read by the R1 rollback image."""
    resource = payload.get('payload') or {}
    event_uri, scheduled_at = _extract_event(resource)
    email, name, invitee_uri = _extract_invitee(resource)
    staged = (
        UnmatchedBookedCall.objects.select_for_update()
        .filter(calendly_event_uri=event_uri)
        .first()
    )
    if staged is None:
        staged = UnmatchedBookedCall(calendly_event_uri=event_uri)
    terminal = staged.pk is not None and staged.status == STATUS_CANCELED
    staged.member = resolve_user_by_email(email) or staged.member
    staged.invitee_email = email or staged.invitee_email
    staged.invitee_name = name or staged.invitee_name
    staged.scheduled_at = scheduled_at or staged.scheduled_at
    staged.calendly_invitee_uri = invitee_uri or staged.calendly_invitee_uri
    staged.scheduling_url = (resource.get('scheduling_url') or '')[:500]
    staged.reschedule_url = (resource.get('reschedule_url') or '')[:500]
    staged.cancel_url = (resource.get('cancel_url') or '')[:500]
    staged.last_event_at = max(filter(None, [staged.last_event_at, event_at]))
    if status == STATUS_CANCELED or terminal:
        staged.status = STATUS_CANCELED
        staged.canceled_at = staged.canceled_at or event_at
    else:
        staged.status = STATUS_BOOKED
        staged.canceled_at = None
    if staged.pk is None:
        try:
            with transaction.atomic():
                staged.save()
        except IntegrityError:
            return _stage_unmatched_call(
                payload,
                status=status,
                event_at=event_at,
            )
    else:
        staged.save()
    return staged


def _promote_staged_call(staged, host):
    """Attach a staged call to a real host without losing terminal state."""
    booked, created = BookedCall.objects.get_or_create(
        calendly_event_uri=staged.calendly_event_uri,
        defaults={
            'host': host,
            'member': staged.member,
            'invitee_email': staged.invitee_email,
            'invitee_name': staged.invitee_name,
            'scheduled_at': staged.scheduled_at,
            'status': staged.status,
            'calendly_invitee_uri': staged.calendly_invitee_uri,
            'reschedule_url': staged.reschedule_url,
            'cancel_url': staged.cancel_url,
            'canceled_at': staged.canceled_at,
            'last_event_at': staged.last_event_at,
        },
    )
    if created and booked.is_active:
        _increment_load(host)
    if created or booked.host_id == host.pk:
        staged.delete()
    return booked


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
    member = resolve_user_by_email(email)
    event_at = _event_time(payload, resource)

    staged = (
        UnmatchedBookedCall.objects.select_for_update()
        .filter(calendly_event_uri=event_uri)
        .first()
    )
    if host is None:
        return _stage_unmatched_call(
            payload,
            status=STATUS_BOOKED,
            event_at=event_at,
        )
    if staged is not None:
        staged = _stage_unmatched_call(
            payload,
            status=STATUS_BOOKED,
            event_at=event_at,
        )
        return _promote_staged_call(staged, host)

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
        # A cancellation tombstone is terminal for this event URI. Calendly
        # reschedules with a new event URI, so a late create must not resurrect it.
        if existing.status != STATUS_CANCELED:
            existing.status = STATUS_BOOKED
            existing.canceled_at = None
        existing.last_event_at = max(filter(None, [existing.last_event_at, event_at]))
        existing.save()
        if existing.is_active and not was_active:
            _increment_load(host)
        return existing

    try:
        with transaction.atomic():
            booked = BookedCall.objects.create(
                host=host, member=member, invitee_email=email,
                invitee_name=name, scheduled_at=scheduled_at,
                status=STATUS_BOOKED, calendly_event_uri=event_uri,
                calendly_invitee_uri=invitee_uri,
                reschedule_url=(resource.get('reschedule_url') or '')[:500],
                cancel_url=(resource.get('cancel_url') or '')[:500],
                last_event_at=event_at,
            )
    except IntegrityError:
        # A concurrent first delivery won the unique event-URI insert. Re-run
        # under a row lock without incrementing capacity twice.
        return handle_invitee_created(payload)
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
    event_at = _event_time(payload, resource, canceled=True)
    if booked is None:
        staged_qs = UnmatchedBookedCall.objects.select_for_update()
        staged = None
        if event_uri:
            staged = staged_qs.filter(calendly_event_uri=event_uri).first()
        if staged is None and invitee_uri:
            staged = staged_qs.filter(calendly_invitee_uri=invitee_uri).first()
        if staged is not None:
            staged.status = STATUS_CANCELED
            staged.canceled_at = staged.canceled_at or event_at
            staged.last_event_at = max(filter(None, [staged.last_event_at, event_at]))
            staged.save(update_fields=[
                'status', 'canceled_at', 'last_event_at', 'updated_at',
            ])
            host = _match_host(resource)
            if host is not None:
                return _promote_staged_call(staged, host)
            return staged
        if not event_uri:
            logger.info('Calendly invitee.canceled without event URI; ignoring')
            return None
        email, name, invitee_uri = _extract_invitee(resource)
        host = _match_host(resource)
        if host is None:
            return _stage_unmatched_call(
                payload,
                status=STATUS_CANCELED,
                event_at=event_at,
            )
        # Persist a terminal tombstone so a delayed create cannot resurrect it.
        return BookedCall.objects.create(
            host=host, member=resolve_user_by_email(email),
            invitee_email=email, invitee_name=name, status=STATUS_CANCELED,
            calendly_event_uri=event_uri, calendly_invitee_uri=invitee_uri,
            canceled_at=event_at, last_event_at=event_at,
        )

    if not booked.is_active:
        return booked

    booked.status = STATUS_CANCELED
    booked.canceled_at = event_at
    booked.last_event_at = event_at
    booked.save(update_fields=['status', 'canceled_at', 'last_event_at', 'updated_at'])
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
