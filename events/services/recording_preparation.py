"""Draft-only transcript preparation through the existing Hub inbound webhook."""

import logging
from datetime import timedelta
from urllib.parse import quote, urlsplit

import requests
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from events.models import RecordingPreparation
from integrations.config import get_config, site_base_url

logger = logging.getLogger(__name__)
START_BOUND = timedelta(minutes=5)
FINISH_BOUND = timedelta(minutes=60)
ALERT_TOLERANCE = timedelta(minutes=4)


class PreparationError(Exception):
    pass


def _connection():
    url = str(get_config('RECORDING_PREPARATION_HUB_URL', '')).strip().rstrip('/')
    token = str(get_config('RECORDING_PREPARATION_HUB_TOKEN', '')).strip()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query:
        raise PreparationError('hub_url_not_configured')
    if not token:
        raise PreparationError('hub_token_not_configured')
    return url, {'X-Webhook-Token': token}


def _payload(preparation):
    event = preparation.event
    return {
        'schema': 'aisl.recording-preparation.v1',
        'event_id': event.pk,
        'event_slug': event.slug,
        'event_title': event.title,
        'recording_uuid': preparation.recording_uuid,
        'transcript_ready_at': preparation.ready_at.isoformat(),
        'source_url': f'{site_base_url()}/api/events/{event.slug}/preparation-source',
        'draft_only': True,
        'outputs': ['video_chapters', 'workshops_raw_summary'],
    }


def dispatch_preparation(event_id):
    """Replay-safe even after an ambiguous timeout; Hub owns idempotency."""
    with transaction.atomic():
        preparation = RecordingPreparation.objects.select_for_update().select_related('event').get(event_id=event_id)
        if preparation.delivery_id:
            return preparation.delivery_id
        url, headers = _connection()
        headers['X-Delivery-Id'] = f'aisl-recording-preparation-{event_id}'
        response = requests.post(
            url, headers=headers, json=_payload(preparation), timeout=15,
            allow_redirects=False,
        )
        if response.status_code not in (200, 202):
            raise PreparationError(f'hub_http_{response.status_code}')
        receipt = response.json()
        delivery_id = receipt.get('delivery_id')
        if not isinstance(delivery_id, str) or not delivery_id or len(delivery_id) > 255:
            raise PreparationError('hub_invalid_receipt')
        preparation.delivery_id = delivery_id
        preparation.status = 'queued'
        preparation.error_code = ''
        preparation.save()
        return delivery_id


def _timestamp(value):
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is not None and not timezone.is_naive(parsed):
        return parsed
    return None


def refresh_preparation(preparation):
    url, headers = _connection()
    delivery = quote(preparation.delivery_id, safe='')
    response = requests.get(
        f'{url}/deliveries/{delivery}', headers=headers, timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise PreparationError(f'hub_status_http_{response.status_code}')
    _store_receipt(preparation, response.json())


def _store_receipt(preparation, receipt):
    status = receipt.get('status')
    if status not in {'pending', 'queued', 'running', 'finished', 'failed'}:
        raise PreparationError('hub_invalid_status')
    started = _timestamp(receipt.get('started_at'))
    finished = _timestamp(receipt.get('finished_at'))
    artifacts = receipt.get('artifacts')
    _validate_finish(preparation, receipt, status, started, finished, artifacts)
    preparation.status = status
    preparation.started_at = started
    preparation.finished_at = finished
    if isinstance(artifacts, list):
        preparation.artifacts = artifacts
    preparation.error_code = ''
    if status == 'failed':
        preparation.error_code = 'hub_job_failed'
    preparation.save()


def preparation_candidates():
    late_finish = Q(finished_at__gt=F('ready_at') + FINISH_BOUND, finish_alerted_at__isnull=True)
    late_start = Q(started_at__gt=F('ready_at') + START_BOUND, start_alerted_at__isnull=True)
    return RecordingPreparation.objects.filter(
        Q(status__in=['pending', 'queued', 'running'])
        | Q(status='failed', alerted_at__isnull=True)
        | (Q(status='finished') & (late_start | late_finish)),
    ).select_related('event').order_by('updated_at')


def reconcile_preparation(event_id):
    preparation = RecordingPreparation.objects.select_related('event').get(event_id=event_id)
    try:
        if not preparation.delivery_id:
            dispatch_preparation(preparation.event_id)
            preparation.refresh_from_db()
        if preparation.status != 'finished':
            refresh_preparation(preparation)
    except (PreparationError, requests.RequestException, ValueError):
        preparation.error_code = 'hub_dispatch_or_status_unavailable'
        preparation.save(update_fields=['error_code', 'updated_at'])
        logger.warning('Hub preparation unavailable for event %s', preparation.event_id)
    _alert_if_overdue(preparation, timezone.now())


def reconcile_preparations(now=None):
    """Synchronous bounded helper for operator use and targeted verification."""
    for preparation in preparation_candidates()[:20]:
        reconcile_preparation(preparation.event_id)


def check_preparation_deadlines(now):
    start_missing = Q(start_alerted_at__isnull=True, started_at__isnull=True, ready_at__lte=now - START_BOUND - ALERT_TOLERANCE)
    start_late = Q(start_alerted_at__isnull=True, started_at__gt=F('ready_at') + START_BOUND)
    finish_missing = Q(finish_alerted_at__isnull=True, finished_at__isnull=True, ready_at__lte=now - FINISH_BOUND - ALERT_TOLERANCE)
    finish_late = Q(finish_alerted_at__isnull=True, finished_at__gt=F('ready_at') + FINISH_BOUND)
    failed = Q(status='failed', alerted_at__isnull=True)
    rows = RecordingPreparation.objects.filter(start_missing | start_late | finish_missing | finish_late | failed)
    for preparation in rows.select_related('event').order_by('ready_at')[:20]:
        _alert_if_overdue(preparation, now)


def _validate_finish(preparation, receipt, status, started, finished, artifacts):
    if receipt.get('delivery_id') != preparation.delivery_id:
        raise PreparationError('hub_wrong_delivery_receipt')
    if finished and (not started or finished < started):
        raise PreparationError('hub_invalid_timestamps')
    if started and started > timezone.now() + timedelta(seconds=5):
        raise PreparationError('hub_invalid_timestamps')
    if status == 'finished':
        if not started or not finished or not isinstance(artifacts, list) or not artifacts:
            raise PreparationError('hub_incomplete_finish_proof')
        if finished > timezone.now() + timedelta(seconds=5):
            raise PreparationError('hub_invalid_timestamps')


def _missed_deadlines(preparation, now):
    missed = []
    start_missed = now >= preparation.ready_at + START_BOUND + ALERT_TOLERANCE
    finish_missed = now >= preparation.ready_at + FINISH_BOUND + ALERT_TOLERANCE
    if preparation.started_at:
        start_missed = preparation.started_at > preparation.ready_at + START_BOUND
    if preparation.finished_at:
        finish_missed = preparation.finished_at > preparation.ready_at + FINISH_BOUND
    if not preparation.start_alerted_at and start_missed:
        missed.append('start')
    if not preparation.finish_alerted_at and finish_missed:
        missed.append('finish')
    if preparation.status == 'failed' and not preparation.alerted_at:
        missed.append('failed')
    return missed


def _alert_if_overdue(preparation, now):
    from events.services.recording_pipeline_alerts import deliver_pipeline_alert

    missed = _missed_deadlines(preparation, now)
    if not missed:
        return
    event = preparation.event
    subject = f'Recording workshop preparation stalled (event {event.pk})'
    body = (
        f'Transcript ready at {preparation.ready_at.isoformat()}. '
        f'Deadline completion not confirmed for: {", ".join(missed)}; Hub status: {preparation.status}; '
        f'error: {preparation.error_code or "none"}. '
        'Required: start within 5 minutes and finish draft chapters and workshop summary '
        'within 60 minutes. Check the Hub delivery/job and retry safely. '
        f'Studio: {site_base_url()}{event.get_studio_edit_url()}'
    )
    if not deliver_pipeline_alert(event, subject, body):
        return
    preparation.alerted_at = now
    if 'start' in missed:
        preparation.start_alerted_at = now
    if 'finish' in missed:
        preparation.finish_alerted_at = now
    preparation.save(update_fields=['alerted_at', 'start_alerted_at', 'finish_alerted_at', 'updated_at'])
