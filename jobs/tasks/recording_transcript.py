"""
Background task for downloading and parsing Zoom transcripts (issue #1597).

Flow:
1. Ensure a transcript URL: if none is stored, re-list the meeting's
   recordings via the Zoom API to pick up a VTT the webhook missed.
2. Download the VTT from Zoom using the same access-token authentication
   as the MP4 upload, and parse cue text into readable plain text (strip
   the WEBVTT header, cue numbers, timestamps and inline tags; collapse
   the consecutive duplicate caption lines Zoom's rolling captions
   produce).
3. Archive the exact VTT bytes in the private recordings bucket and store
   both ``Event.transcript_text`` and ``Event.transcript_s3_url``.
4. Chain the recap auto-draft when its gates allow (issue #1597).

Retry model: Zoom can deliver ``recording.completed`` before the VTT is
processed. A missing URL, a 404/empty download, or a parse that yields
nothing counts as a not-ready attempt (recorded on
``Event.transcript_fetch_attempts``) and raises ``TranscriptNotReady`` so
django-q2 retries with backoff. When ``TRANSCRIPT_MAX_ATTEMPTS`` is
reached the event is marked unavailable instead — the terminal
no-transcript state — and recap drafting is skipped. The task is
idempotent: a transcript with both durable forms short-circuits straight
to the recap step, so webhook replays and django-q2 retries are safe.
"""

import logging
import re

import requests

from jobs.tasks.recordings_s3 import (
    build_transcript_s3_key,
    get_recordings_s3_config,
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
    """Raised with a bounded message when private VTT archival fails."""


def parse_vtt_to_text(raw):
    """Parse a WebVTT document into readable plain text.

    Strips the ``WEBVTT`` header, ``NOTE``/``STYLE``/``REGION`` blocks, cue
    numbering and timestamp lines, and inline tags (``<v Speaker>``,
    ``<c.class>``, inline ``<00:00:01.000>`` timestamps). Consecutive
    duplicate caption lines — Zoom's rolling captions repeat the current
    line until it changes — are collapsed to the last occurrence, keeping
    first-seen order otherwise. Blank lines and cue boundaries vanish, so
    the result is one caption line per line of text.
    """
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8-sig', errors='replace')

    lines = []
    in_note = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if in_note:
            # NOTE/STYLE/REGION blocks run until the next blank line.
            if not line:
                in_note = False
            continue
        if not line:
            continue
        header = line.split(' ', 1)[0] if ' ' in line else line
        if header in ('WEBVTT', 'NOTE', 'STYLE', 'REGION'):
            # WEBVTT header may carry metadata on the same line; block
            # keywords (NOTE/STYLE/REGION) continue until a blank line.
            if header != 'WEBVTT':
                in_note = True
            continue
        if '-->' in line:
            # Cue timing line, e.g. "00:00:15.240 --> 00:00:18.660".
            continue
        if re.fullmatch(r'\d+', line):
            # Bare cue-number line.
            continue
        text = re.sub(r'<[^>]+>', '', line).strip()
        if not text:
            continue
        if lines and lines[-1] == text:
            # Rolling-caption repeat of the line we just kept.
            continue
        lines.append(text)
    return '\n'.join(lines)


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
    """Download, parse, and store the Zoom transcript; chain recap draft.

    Args:
        event_id: ID of the Event model instance.
        redraft: When True, regenerate the recap draft even if
            ``recap_notes`` is non-empty (explicit operator opt-in via the
            sync-transcript surfaces).

    Returns:
        dict with ``status`` (``ok`` / ``already_stored`` / ``unavailable``
        / ``skipped``) and detail keys. Only unexpected errors raise, so
        django-q2 retries them; the not-ready path manages its own retry
        budget and terminal state.
    """
    from events.models import Event
    from events.services.recording_transcript import (
        refresh_transcript_from_zoom,
    )
    from integrations.services.zoom import ZoomAPIError

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        logger.error('Event %s not found, skipping transcript capture', event_id)
        return {'status': 'error', 'message': f'Event {event_id} not found'}

    if event.transcript_text and event.transcript_s3_url:
        logger.info(
            'Transcript already stored for event "%s" (id=%s)',
            event.title, event.id,
        )
        recap_result = _draft_recap(event, redraft)
        return {
            'status': 'already_stored',
            'event_id': event_id,
            'recap': recap_result,
        }

    # Step 1: make sure we have a transcript URL to fetch.
    if not event.transcript_url:
        try:
            refresh_transcript_from_zoom(event)
        except ZoomAPIError as exc:
            logger.warning(
                'Zoom recordings re-list failed for event "%s" (id=%s): %s',
                event.title, event.id, exc,
            )

    # Step 2: fetch + parse, or run the retry/terminal machinery.
    if not event.transcript_url:
        if event.transcript_text:
            raise TranscriptArchiveNotReady(
                f'VTT archive source not ready for event {event.id}',
            )
        return _handle_not_ready(event, 'no_transcript_url')

    try:
        raw_vtt = _download_vtt(event.transcript_url)
    except requests.RequestException as exc:
        if event.transcript_text:
            raise TranscriptArchiveNotReady(
                f'VTT archive source not ready for event {event.id}',
            ) from None
        status_code = getattr(exc.response, 'status_code', None)
        reason = (
            f'download_failed_{status_code}'
            if status_code is not None
            else 'download_failed'
        )
        return _handle_not_ready(event, reason)

    parsed_vtt_text = parse_vtt_to_text(raw_vtt)
    if not parsed_vtt_text:
        if event.transcript_text:
            raise TranscriptArchiveNotReady(
                f'VTT archive source not ready for event {event.id}',
            )
        # A parse that yields nothing cannot be fixed by storing it; count
        # it as a not-ready attempt so the retry budget still applies.
        return _handle_not_ready(event, 'empty_parse')
    transcript_text = event.transcript_text or parsed_vtt_text

    # Keep #1597's parsed text durable even if the new archive write fails.
    # A retry then fills only the missing archive without replacing the text.
    text_update_fields = []
    if not event.transcript_text:
        event.transcript_text = transcript_text
        text_update_fields.append('transcript_text')
    if event.transcript_fetch_attempts:
        event.transcript_fetch_attempts = 0
        text_update_fields.append('transcript_fetch_attempts')
    if text_update_fields:
        text_update_fields.append('updated_at')
        event.save(update_fields=text_update_fields)

    if not event.transcript_s3_url:
        event.transcript_s3_url = _archive_vtt(event, raw_vtt)
        event.save(update_fields=['transcript_s3_url', 'updated_at'])
    logger.info(
        'Stored %d characters of transcript text for event "%s" (id=%s)',
        len(transcript_text), event.title, event.id,
    )

    recap_result = _draft_recap(event, redraft)
    return {
        'status': 'ok',
        'event_id': event_id,
        'characters': len(transcript_text),
        'recap': recap_result,
    }


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
