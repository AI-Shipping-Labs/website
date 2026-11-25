"""Independent recording phases, fair provider reconciliation and deadline alerts.

Dapier recording.completed supplies the original MP4 readiness timestamp.
A one-minute Zoom poll recovers missing delivery and records first observation;
it never invents an earlier availability timestamp. Local deadline checks run
before provider tasks are queued. Relay campaign confirmation, not EmailLog
submission, completes Stage 1. Hub draft artifact proof completes Stage 2.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from django.db.models import F, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

RECORDING_POLL_INTERVAL = timedelta(minutes=1)
RECORDING_OBSERVATION_GRACE = timedelta(minutes=4)
RECORDING_READY_BOUND = timedelta(minutes=60)
# Zoom cannot have a finished MP4 before the meeting has run a while; skip
# the API call for events that only just started.
RECORDING_POLL_START_DELAY = timedelta(minutes=10)
# Stop polling events that never produced a recording.
RECORDING_POLL_LOOKBACK = timedelta(days=3)
RECORDING_POLL_LIMIT = 20
# A recording listed for a recurring meeting ID must belong to this
# occurrence: its start has to fall inside this window around the event.
RECORDING_INSTANCE_EARLY_START = timedelta(hours=3)
# Only alert for availability stamps this recent; older rows predate the
# watch or were already handled by hand.
RECORDING_STALL_LOOKBACK = timedelta(days=7)
RECORDING_STALL_ALERT_LIMIT = 20

RECORDING_POLL_SOURCE = 'Zoom recording poll'
RECORDING_STALL_DEV_RECIPIENT = 'dev@dtcdev.click'
RECORDING_READY_EMAIL_TYPE = 'event_recording_ready'


def run_recording_pipeline_watch(now=None):
    """Check local deadlines first; provider work has independent task budgets."""
    from events.models import Event
    from events.services.recording_preparation import check_preparation_deadlines, preparation_candidates
    from jobs.tasks import async_task

    if now is None:
        now = timezone.now()
    stalls = check_recording_pipeline_stalls(now=now)
    check_preparation_deadlines(now)
    candidates = list(poll_candidates(now).values_list('pk', flat=True)[:20])
    for event_id in candidates:
        async_task('events.services.recording_pipeline_watch.poll_event_task', event_id, timeout=90)
    preparations = preparation_candidates().values_list('event_id', flat=True)[:20]
    for event_id in preparations:
        async_task('events.services.recording_preparation.reconcile_preparation', event_id, timeout=60)
    notices = Event.objects.filter(recording_zoom_available_at__isnull=False, recording_ready_completed_at__isnull=True).exclude(recording_s3_url='')
    for event_id in notices.values_list('pk', flat=True)[:20]:
        async_task('events.services.recording_pipeline_watch.refresh_host_notice', event_id, timeout=60)
    return {'poll': {'examined': len(candidates)}, 'stalls': stalls}


def poll_event_task(event_id):
    from events.models import Event

    return _poll_event(Event.objects.get(pk=event_id))


def poll_candidates(now):
    """Events whose Zoom MP4 may be ready but the platform has not seen yet."""
    from events.models import Event

    return (
        Event.objects.exclude(zoom_meeting_id='')
        .exclude(status='cancelled')
        .filter(
            start_datetime__lte=now - RECORDING_POLL_START_DELAY,
            start_datetime__gte=now - RECORDING_POLL_LOOKBACK,
        )
        .filter(Q(recording_s3_url='') | Q(transcript_text=''))
        .order_by('-start_datetime', 'id')
    )


def poll_zoom_recordings(now=None, limit=RECORDING_POLL_LIMIT):
    """Ask Zoom for finished MP4s and start their S3 uploads.

    Reuses :func:`refresh_transcript_from_zoom`, the same re-list the
    sync-transcript intake uses, so the MP4 choice, the duplicate-upload
    guard and the lease are identical whichever path sees the file first.
    A 404 from Zoom means "no recording yet" and is not an error.
    """
    if now is None:
        now = timezone.now()
    counts = {'examined': 0, 'queued': 0, 'not_ready': 0, 'errors': 0}
    for event in poll_candidates(now)[:limit]:
        counts['examined'] += 1
        outcome = _poll_event(event)
        counts[outcome] += 1
    return counts


def _poll_event(event):
    from events.services.recording_transcript import refresh_transcript_from_zoom
    from integrations.services.zoom import ZoomAPIError, get_meeting_recordings

    try:
        data = get_meeting_recordings(event)
        if not recording_matches_event(data, event):
            return 'not_ready'
        refreshed = refresh_transcript_from_zoom(event, source=RECORDING_POLL_SOURCE, data=data)
        _discover_transcript(event)
    except ZoomAPIError as exc:
        if exc.status_code == 404:
            return 'not_ready'
        _poll_error(event)
        return 'errors'
    except Exception:
        # External provider boundary: keep other events moving and alert staff.
        _poll_error(event)
        return 'errors'
    if refreshed.get('upload_queued'):
        return 'queued'
    return 'not_ready'


def _poll_error(event):
    from events.services.recording_pipeline_alerts import deliver_pipeline_alert

    logger.warning('Zoom recording discovery failed for event %s', event.pk)
    deliver_pipeline_alert(
        event, f'Recording discovery failed (event {event.pk})',
        'Zoom recording readiness could not be checked. The bounded upload and '
        'host notification cannot be verified. Check Zoom credentials and the recording worker.',
    )


def recording_matches_event(data, event):
    """True when the listed recording belongs to this event occurrence.

    ``GET /meetings/{id}/recordings`` returns the latest instance of a
    recurring meeting ID, which can be an earlier occurrence. A payload
    without ``start_time`` is rejected because its occurrence is unproven.
    """
    raw_start = str((data or {}).get('start_time') or '').strip()
    if not raw_start:
        return False
    try:
        recording_start = datetime.fromisoformat(raw_start.replace('Z', '+00:00'))
    except ValueError:
        return False
    if timezone.is_naive(recording_start):
        recording_start = recording_start.replace(tzinfo=UTC)
    earliest = event.start_datetime - RECORDING_INSTANCE_EARLY_START
    return earliest <= recording_start <= event.effective_end_datetime


def stalled_events(now):
    """Events past the bound without an S3 recording or a host email."""
    from events.models import Event

    return Event.objects.filter(
        recording_zoom_available_at__isnull=False,
        recording_stall_alerted_at__isnull=True,
    ).filter(
        Q(
            recording_ready_completed_at__isnull=True,
            recording_zoom_available_at__lte=now - RECORDING_READY_BOUND - RECORDING_OBSERVATION_GRACE,
        )
        | Q(recording_ready_completed_at__gt=F('recording_zoom_available_at') + RECORDING_READY_BOUND),
    ).order_by('recording_zoom_available_at', 'id')


def check_recording_pipeline_stalls(now=None, limit=RECORDING_STALL_ALERT_LIMIT):
    """Alert staff once per event when the recording-ready bound is missed.

    The marker is only stored after a route (staff Slack, then the dev
    mailbox) accepted the alert, so a failed alert is retried on the next
    pass instead of being dropped.
    """
    if now is None:
        now = timezone.now()
    alerted = 0
    failed = 0
    for event in stalled_events(now)[:limit]:
        if now < event.recording_zoom_available_at + RECORDING_READY_BOUND:
            continue
        if _deliver_stall_alert(event, now):
            event.recording_stall_alerted_at = now
            event.save(update_fields=['recording_stall_alerted_at', 'updated_at'])
            alerted += 1
        else:
            failed += 1
    return {'alerted': alerted, 'failed': failed}


def stall_alert_text(event, now):
    """Subject and body for a stall alert. No S3 or Zoom URLs, no emails."""
    from events.services.recording_upload import recording_upload_status
    from integrations.config import site_base_url

    if not event.recording_s3_url:
        stalled_step = 'S3 upload'
    else:
        stalled_step = 'host "Recording ready for review" email'
    waited = int((now - event.recording_zoom_available_at).total_seconds() // 60)
    bound = int(RECORDING_READY_BOUND.total_seconds() // 60)
    subject = f'Recording pipeline stalled: {event.title} (event {event.pk})'
    body = (
        f'Zoom video for event {event.pk} "{event.title}" has been available '
        f'for {waited} minutes, but the {stalled_step} was not confirmed complete within the bound '
        f'(bound: {bound} minutes).\n'
        f'Upload status: {recording_upload_status(event, now=now)}\n'
        f'Studio: {site_base_url()}{event.get_studio_edit_url()}\n'
        'Next step: check the worker task, then POST '
        f'/api/events/{event.slug}/retry-recording-upload or resend the host '
        'email from Studio.'
    )
    return subject, body



def _host_campaign_completed(event):
    from email_app.services.relay_campaigns import RelayCampaignError, get_native_campaign_stats
    from events.services.recording_ready_notification import resolve_recording_ready_recipients

    try:
        stats = get_native_campaign_stats(f'event-recording-ready-{event.pk}', allow_not_found=True)
    except RelayCampaignError:
        return False
    if not stats:
        return False
    expected = len(resolve_recording_ready_recipients(event))
    return expected > 0 and stats.get('sent_count', 0) >= expected and stats.get('pending_count', 0) == 0


def _deliver_stall_alert(event, now):
    from events.services.recording_pipeline_alerts import deliver_pipeline_alert

    subject, body = stall_alert_text(event, now)
    return deliver_pipeline_alert(event, subject, body)


def refresh_host_notice(event_id):
    from email_app.models import EmailLog
    from events.models import Event
    from events.services.recording_ready_notification import notify_recording_ready

    event = Event.objects.get(pk=event_id)
    if not event.recording_s3_url:
        return
    if not EmailLog.objects.filter(event=event, email_type=RECORDING_READY_EMAIL_TYPE).exists():
        notify_recording_ready(event)
    if _host_campaign_completed(event):
        event.recording_ready_completed_at = timezone.now()
        event.save(update_fields=['recording_ready_completed_at', 'updated_at'])


def _discover_transcript(event):
    from events.models import RecordingPreparation
    from events.services.recording_transcript import enqueue_recording_transcript_task

    if not event.transcript_url or event.transcript_text:
        return
    preparation, created = RecordingPreparation.objects.get_or_create(
        event=event, defaults={'ready_at': timezone.now()},
    )
    if created:
        enqueue_recording_transcript_task(event, source='Zoom transcript reconciliation')
