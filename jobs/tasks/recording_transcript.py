"""Archive Zoom transcripts; automatic completions prepare draft materials in Hub.

Provider-not-ready attempts use the existing bounded django-q retry policy.
Explicit operator redraft requests retain their legacy recap behavior.
"""

import logging

import requests

from jobs.tasks.recording_vtt import parse_vtt_to_text as parse_vtt_to_text
from jobs.tasks.recordings_s3 import (
    build_transcript_s3_key,
    build_transcript_text_s3_key,
    get_recordings_s3_config,
    upload_transcript_text,
    upload_transcript_vtt,
)

logger = logging.getLogger(__name__)

# Keep the retry constants in sync with
# events/services/recording_transcript.py, which passes them to django-q.
TRANSCRIPT_TASK_TIMEOUT_SECONDS = 300
TRANSCRIPT_HTTP_TIMEOUT_SECONDS = 60
TRANSCRIPT_TASK_RETRY_SECONDS = 900
TRANSCRIPT_MAX_RETRIES = 3
TRANSCRIPT_MAX_ATTEMPTS = TRANSCRIPT_MAX_RETRIES + 1


class TranscriptNotReady(Exception):
    """Raised when the VTT is not (yet) available; django-q2 retries."""


class TranscriptArchiveNotReady(Exception):
    """Raised when existing text cannot yet be paired with its raw archive."""


class TranscriptArchiveError(Exception):
    """Raised with a bounded message when private transcript archival fails."""


def _build_authenticated_download_url(download_url):
    """Add the Zoom OAuth access token to a download URL.

    Mirrors the MP4 upload path: Zoom download URLs require the
    ``access_token`` query parameter.
    """
    from integrations.services.zoom import get_access_token

    token = get_access_token()
    separator = '&' if '?' in download_url else '?'
    return f'{download_url}{separator}access_token={token}'


def _download_vtt(download_url):
    """Fetch the VTT document and return its exact bytes."""
    response = requests.get(
        _build_authenticated_download_url(download_url),
        timeout=TRANSCRIPT_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.content


def _archive_vtt(event, raw_vtt):
    """Store the raw VTT at its deterministic private recordings key."""
    config = get_recordings_s3_config()
    if not config.bucket:
        raise TranscriptArchiveError('Recordings S3 bucket is not configured')
    raw_bytes = raw_vtt if isinstance(raw_vtt, bytes) else raw_vtt.encode('utf-8')
    try:
        return upload_transcript_vtt(
            raw_bytes,
            config,
            build_transcript_s3_key(event),
        )
    except Exception:
        raise TranscriptArchiveError(
            f'VTT archival failed for event {event.id}',
        ) from None


def _archive_transcript_text(event, transcript_text):
    """Store parsed transcript text at its deterministic private S3 key."""
    config = get_recordings_s3_config()
    if not config.bucket:
        raise TranscriptArchiveError('Recordings S3 bucket is not configured')
    try:
        return upload_transcript_text(
            transcript_text,
            config,
            build_transcript_text_s3_key(event),
        )
    except Exception:
        raise TranscriptArchiveError(
            f'Text archival failed for event {event.id}',
        ) from None


def _handle_not_ready(event, reason):
    """Record a failed attempt; retry via django-q or mark unavailable.

    Returns the terminal result dict when the attempt budget is exhausted
    (recap drafting skipped, warning logged); otherwise raises
    :class:`TranscriptNotReady` so django-q2 retries with backoff.
    """
    from events.services.recording_transcript import (
        mark_transcript_unavailable,
    )

    attempts = event.transcript_fetch_attempts + 1
    if attempts >= TRANSCRIPT_MAX_ATTEMPTS:
        mark_transcript_unavailable(event, attempts=attempts)
        logger.warning(
            'No transcript available for event "%s" (id=%s) after %d '
            'attempts (%s); marked unavailable and skipping recap drafting',
            event.title, event.id, attempts, reason,
        )
        return {
            'status': 'unavailable',
            'event_id': event.id,
            'attempts': attempts,
            'reason': reason,
        }
    event.transcript_fetch_attempts = attempts
    event.save(update_fields=['transcript_fetch_attempts', 'updated_at'])
    logger.info(
        'Transcript not ready for event "%s" (id=%s), attempt %d/%d (%s); '
        'will retry',
        event.title, event.id, attempts, TRANSCRIPT_MAX_ATTEMPTS, reason,
    )
    raise TranscriptNotReady(
        f'Transcript not ready for event {event.id}: {reason}',
    )


def transcribe_recording(event_id, redraft=False):
    """Archive a transcript, then prepare review drafts through Hub."""
    from events.models import Event

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        return {'status': 'error', 'message': f'Event {event_id} not found'}
    if event.transcript_text and event.transcript_s3_url:
        return {
            'status': 'already_stored', 'event_id': event_id,
            'recap': _after_transcript(event, redraft),
        }
    _refresh_transcript_source(event)
    raw_vtt, parsed, terminal = _capture_transcript(event)
    if terminal is not None:
        return terminal
    transcript_text = event.transcript_text or parsed
    _store_transcript_text(event, transcript_text)
    _archive_captured_transcript(event, raw_vtt, transcript_text)
    return {
        'status': 'ok', 'event_id': event_id,
        'characters': len(transcript_text),
        'recap': _after_transcript(event, redraft),
    }


def _refresh_transcript_source(event):
    from events.services.recording_transcript import refresh_transcript_from_zoom
    from integrations.services.zoom import ZoomAPIError

    if event.transcript_url:
        return
    try:
        refresh_transcript_from_zoom(event)
    except ZoomAPIError:
        logger.warning('Zoom transcript source not ready for event %s', event.pk)


def _unavailable_capture(event, reason):
    if event.transcript_text:
        raise TranscriptArchiveNotReady(f'VTT archive source not ready for event {event.id}')
    return None, None, _handle_not_ready(event, reason)


def _capture_transcript(event):
    if not event.transcript_url:
        return _unavailable_capture(event, 'no_transcript_url')
    try:
        raw_vtt = _download_vtt(event.transcript_url)
    except requests.RequestException as exc:
        reason = 'download_failed'
        status_code = getattr(exc.response, 'status_code', None)
        if status_code is not None:
            reason = f'download_failed_{status_code}'
        return _unavailable_capture(event, reason)
    parsed = parse_vtt_to_text(raw_vtt)
    if not parsed:
        return _unavailable_capture(event, 'empty_parse')
    return raw_vtt, parsed, None


def _store_transcript_text(event, text):
    # Preserve parsed text even when private archival needs a retry.
    fields = []
    if not event.transcript_text:
        event.transcript_text = text
        fields.append('transcript_text')
    if event.transcript_fetch_attempts:
        event.transcript_fetch_attempts = 0
        fields.append('transcript_fetch_attempts')
    if fields:
        event.save(update_fields=[*fields, 'updated_at'])


def _archive_captured_transcript(event, raw_vtt, text):
    transcript_s3_url = event.transcript_s3_url
    if not transcript_s3_url:
        transcript_s3_url = _archive_vtt(event, raw_vtt)
    _archive_transcript_text(event, text)
    if not event.transcript_s3_url:
        event.transcript_s3_url = transcript_s3_url
        event.save(update_fields=['transcript_s3_url', 'updated_at'])


def _draft_recap(event, redraft):
    """Chain the recap auto-draft; best-effort by contract.

    The recap runs as its own django-q task (its own retry budget for
    transient LLM errors, and the transcript task stays fast). Gates are
    re-checked at enqueue and again at run time, so notes an operator
    saves in between are never overwritten.
    """
    from events.services.recap_draft import (
        enqueue_recap_draft_task,
        recap_draft_gate,
    )

    allowed, reason = recap_draft_gate(event, force=redraft)
    if not allowed:
        logger.info(
            'Recap draft not chained for event "%s" (id=%s): %s',
            event.title, event.id, reason,
        )
        return {'status': 'skipped', 'reason': reason}
    task_id = enqueue_recap_draft_task(
        event, source='Transcript capture', force=redraft,
    )
    return {'status': 'queued', 'task_id': task_id, 'forced': redraft}


def _after_transcript(event, redraft):
    from django.utils import timezone

    from events.models import RecordingPreparation
    from events.services.recording_preparation import dispatch_preparation

    if redraft:
        return _draft_recap(event, redraft)
    RecordingPreparation.objects.get_or_create(
        event=event, defaults={'ready_at': timezone.now()},
    )
    delivery_id = dispatch_preparation(event.pk)
    return {'status': 'hub_draft_queued', 'delivery_id': delivery_id}
