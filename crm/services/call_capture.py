"""Post-meeting capture for booked 1:1 calls (issue #1951).

After a booked meeting ends, this service pulls the Zoom recording and
transcript, refreshes the call's summary through the LLM, and stores a
DRAFT attendee email on the CRM call row. It NEVER sends email in any
configuration: the only send path is the explicit Studio button
(``studio.views.crm.crm_call_send_email``), which stamps
``email_sent_at``.

Retry model (mirrors ``events``/``jobs.tasks.recording_transcript``):
Zoom can deliver the recording before the VTT is processed, so a
not-ready fetch increments ``capture_attempts`` and is retried on the
next tick; after ``CAPTURE_MAX_ATTEMPTS`` (1 initial + 3 retries) the
call is marked ``capture_unavailable_at`` -- the terminal state that
takes it out of selection. No recording files at all, and a booking
without a ``zoom_meeting_id``, go straight to the terminal marker
instead of retrying forever.

Exactly-once: a call with a stored draft (``email_draft_ready_at``) or
a sent email is never re-selected, so a second tick for the same
booking is a no-op.
"""

import datetime as dt
import logging

from django.utils import timezone

from booking.models.booking import STATUS_CONFIRMED, Booking
from crm.models.call import Call
from crm.services.calls import record_call
from integrations.config import crm_call_capture_enabled
from integrations.services import llm
from integrations.services.llm import LLMError
from integrations.services.zoom import ZoomAPIError

logger = logging.getLogger(__name__)

#: 1 initial tick + 3 retries, mirroring the events transcript budget
#: (``TRANSCRIPT_MAX_ATTEMPTS = TRANSCRIPT_MAX_RETRIES + 1``).
CAPTURE_MAX_RETRIES = 3
CAPTURE_MAX_ATTEMPTS = CAPTURE_MAX_RETRIES + 1

#: How far back a finished meeting is still worth capturing; bounds the
#: first run after deploy so the tick never scans all history.
CAPTURE_LOOKBACK_DAYS = 7

#: Fixed character cap on the transcript fed to the LLM (same safety
#: bound as the event recap draft; not an operator knob).
CALL_SUMMARY_MAX_TRANSCRIPT_CHARS = 60_000

CALL_SUMMARY_TRUNCATION_NOTE = (
    '\n\n[Transcript truncated at the character limit -- the remainder '
    'of the call is not included. Summarize only what is present.]'
)

# The system prompt is versioned as a module constant so prompt
# revisions are diffable. Guardrails (issue #1951): transcript facts
# only, no invented quotes, no attendee PII, plain-text email a host
# could actually send.
CALL_SUMMARY_SYSTEM_PROMPT = (
    'You write the post-meeting summary email that an AI Shipping Labs '
    'host sends to the attendee of a recorded 1:1 call. You are given '
    'the verbatim transcript of one call. Write the email body in '
    'plain text (no HTML, no Markdown headings):\n'
    '- A one-line opening naming what the call was about.\n'
    '- A short summary of the main points and the flow of the '
    'conversation.\n'
    '- Concrete next steps: the exact tools, commands, links, or '
    'homework actually named in the transcript. Omit the section if '
    'the transcript gives nothing actionable.\n'
    '- A brief closing inviting follow-up questions.\n\n'
    'Hard rules:\n'
    '- Use only facts stated in the transcript. Never invent claims, '
    'numbers, links, project names, or quotes that are not in it. '
    'Short verbatim phrases are fine; never fabricate an exact '
    'quotation or attribute a statement to someone who did not make '
    'it.\n'
    '- Include no personal data: no email addresses, usernames, '
    'employers, locations, or other personal details. Refer to people '
    'generically; the host may be named to attribute presented '
    'content.\n'
    '- If the transcript is too sparse to summarize, say so in one '
    'sentence rather than padding.'
)

CALL_SUMMARY_MAX_TOKENS = 2048

# Derived capture statuses (mirrors events' transcript_status).
CAPTURE_STATUS_NONE = 'none'
CAPTURE_STATUS_WAITING = 'waiting'
CAPTURE_STATUS_STORED = 'stored'
CAPTURE_STATUS_UNAVAILABLE = 'unavailable'


def capture_status(call):
    """Derived operator status for one call's capture state.

    ``stored`` (transcript parsed), ``unavailable`` (terminal marker),
    ``waiting`` (attempts recorded, still retrying), ``none``
    (untouched).
    """
    if call.transcript_text:
        return CAPTURE_STATUS_STORED
    if call.capture_unavailable_at is not None:
        return CAPTURE_STATUS_UNAVAILABLE
    if call.capture_attempts:
        return CAPTURE_STATUS_WAITING
    return CAPTURE_STATUS_NONE


def calls_pending_capture(now=None):
    """Booking-source calls that may still need capture, oldest first.

    Excludes calls that already carry a draft, were sent, or hit the
    terminal unavailable marker. The booking-level rules (confirmed,
    finished within the lookback window) are enforced per call in
    :func:`capture_call` because the booking is resolved per row.
    """
    now = now or timezone.now()
    return (
        Call.objects.filter(source='booking')
        .exclude(source_ref='')
        .filter(
            email_draft_ready_at__isnull=True,
            email_sent_at__isnull=True,
            capture_unavailable_at__isnull=True,
        )
        .select_related('contact__user')
        .order_by('occurred_at', 'id')
    )


def booking_for_call(call):
    """Resolve the :class:`Booking` behind a booking-source call.

    ``booking.services.booking_service`` writes
    ``source_ref=str(booking.id)``; the ``manage_token`` form is the
    documented contract for machine writers, so both resolve.
    """
    booking = Booking.objects.filter(manage_token=call.source_ref).first()
    if booking is None and call.source_ref.isdigit():
        booking = Booking.objects.filter(pk=int(call.source_ref)).first()
    return booking


def capture_finished_booking_calls(now=None):
    """One scheduled tick over every pending booking call.

    Sequential and bounded by the lookback window. Returns a tally
    dict; expected failures are logged, never raised.
    """
    if not crm_call_capture_enabled():
        logger.info('booking-call-capture disabled; tick skipped')
        return {'status': 'disabled', 'selected': 0}

    now = now or timezone.now()
    tally = {
        'status': 'ok',
        'selected': 0,
        'drafted': 0,
        'not_ready': 0,
        'unavailable': 0,
        'skipped': 0,
        'error': 0,
    }
    for call in calls_pending_capture(now):
        tally['selected'] += 1
        result = capture_call(call, now=now)
        key = result['status']
        if key not in tally:
            tally[key] = 0
        tally[key] += 1
        logger.info(
            'booking-call-capture call id=%s source_ref=%s: %s',
            call.pk, call.source_ref, result,
        )
    return tally


def capture_call(call, *, now=None):
    """Advance one call's capture; returns a status dict.

    Never raises for expected failures (Zoom not configured, recording
    not processed yet, LLM error) -- those are logged and retried on a
    later tick or end in the terminal unavailable state.
    """
    now = now or timezone.now()
    booking = booking_for_call(call)
    if booking is None:
        # Orphaned call (booking gone): ignored, never marked.
        return {'status': 'skipped', 'reason': 'no_booking'}
    if booking.status != STATUS_CONFIRMED:
        return {'status': 'skipped', 'reason': 'booking_not_confirmed'}
    if booking.end_datetime >= now:
        return {'status': 'skipped', 'reason': 'meeting_not_finished'}
    if booking.end_datetime < now - dt.timedelta(days=CAPTURE_LOOKBACK_DAYS):
        return {'status': 'skipped', 'reason': 'outside_lookback_window'}
    if not booking.zoom_meeting_id:
        # Nothing to ever pull: terminal, never an error loop (AC10).
        return _mark_unavailable(call, 'no_zoom_meeting_id', now=now)

    if not call.transcript_text:
        transcript_result = _capture_transcript(call, booking, now=now)
        if transcript_result['status'] != 'stored':
            return transcript_result
        call.refresh_from_db()

    return _draft_summary_email(call, booking)


def _capture_transcript(call, booking, *, now=None):
    """List recordings, fetch the VTT, and store transcript state."""
    try:
        data = get_meeting_recordings(booking)
    except ZoomAPIError as exc:
        logger.warning(
            'Zoom recordings list failed for booking %s (call %s): %s; '
            'will retry next tick',
            booking.pk, call.pk, exc,
        )
        return {'status': 'error', 'reason': 'zoom_api_error'}

    recording_files = data.get('recording_files', [])
    if not recording_files:
        # Zoom processed the meeting with no recording at all (cloud
        # recording off): terminal, not retryable (AC10).
        return _mark_unavailable(call, 'no_recording_files', now=now)

    transcript_url = _extract_transcript_url(recording_files)
    if not transcript_url:
        return _record_failed_attempt(call, 'no_transcript_url', now=now)

    try:
        raw_vtt = _download_vtt(transcript_url)
        transcript_text = parse_transcript_vtt(raw_vtt)
    except Exception as exc:  # network/HTTP failures are not-ready
        logger.warning(
            'Transcript download failed for booking %s (call %s): %s',
            booking.pk, call.pk, exc,
        )
        return _record_failed_attempt(
            call, 'download_failed', now=now,
        )
    if not transcript_text:
        return _record_failed_attempt(call, 'empty_parse', now=now)

    call.transcript_text = transcript_text
    call.recording_url = _extract_share_url(data)
    call.capture_attempts = 0
    call.save(update_fields=[
        'transcript_text', 'recording_url', 'capture_attempts',
        'updated_at',
    ])
    logger.info(
        'Stored %d characters of transcript for call %s (booking %s)',
        len(transcript_text), call.pk, booking.pk,
    )
    return {'status': 'stored', 'characters': len(transcript_text)}


def _draft_summary_email(call, booking, *, now=None):
    """Generate the LLM summary and store it with the DRAFT email."""
    del now
    if not crm_call_capture_enabled():
        return {'status': 'skipped', 'reason': 'capture_disabled'}
    if not llm.is_enabled():
        logger.info(
            'Call summary skipped for call %s: LLM not configured; '
            'the existing booking summary stays untouched',
            call.pk,
        )
        return {'status': 'skipped', 'reason': 'llm_not_configured'}

    summary_result = _generate_summary(booking, call.transcript_text)
    if summary_result['status'] != 'ok':
        # Log-and-continue: never raises, the existing booking summary
        # stays untouched, and a later tick retries generation.
        return summary_result
    summary = summary_result['summary']

    # The canonical upsert refreshes occurred_at after reschedules and
    # re-adopts the contact link; the same (source, source_ref) write
    # enriches the row the booking app created.
    updated_call = record_call(
        email=booking.invitee_email,
        occurred_at=booking.start_datetime,
        summary=summary,
        source='booking',
        source_ref=call.source_ref,
    )
    updated_call.email_draft_subject = draft_subject(booking)
    updated_call.email_draft_body = summary
    updated_call.email_draft_ready_at = timezone.now()
    updated_call.save(update_fields=[
        'email_draft_subject', 'email_draft_body', 'email_draft_ready_at',
        'updated_at',
    ])
    logger.info(
        'Stored attendee email draft for call %s (booking %s)',
        updated_call.pk, booking.pk,
    )
    return {
        'status': 'drafted',
        'call_id': updated_call.pk,
        'characters': len(summary),
    }


def draft_subject(booking):
    """``Your 1:1 with <host> — <Mon DD>`` (issue #1951)."""
    return (
        f'Your 1:1 with {booking.host.name} — '
        f'{booking.start_datetime.strftime("%b %d")}'
    )


def _generate_summary(booking, transcript_text):
    """Run the LLM over the bounded transcript; never raises."""
    transcript = transcript_text or ''
    if len(transcript) > CALL_SUMMARY_MAX_TRANSCRIPT_CHARS:
        transcript = (
            transcript[:CALL_SUMMARY_MAX_TRANSCRIPT_CHARS]
            + CALL_SUMMARY_TRUNCATION_NOTE
        )
    context_lines = [
        f'Host: {booking.host.name}',
        f'Call date: {booking.start_datetime.date().isoformat()}',
    ]
    if (booking.discussion_topic or '').strip():
        context_lines.append(
            f'Scheduled topic: {booking.discussion_topic.strip()}',
        )
    user_message = '\n'.join([
        *context_lines,
        '',
        'Transcript follows:',
        '',
        transcript,
    ])
    try:
        result = llm.complete(
            [{'role': 'user', 'content': user_message}],
            system=CALL_SUMMARY_SYSTEM_PROMPT,
            max_tokens=CALL_SUMMARY_MAX_TOKENS,
        )
    except LLMError as exc:
        logger.error(
            'Call summary LLM call failed for booking %s: %s',
            booking.pk, exc,
        )
        return {'status': 'error', 'reason': 'llm_error'}
    summary = (result.text or '').strip()
    if not summary:
        logger.error(
            'Call summary LLM returned empty output for booking %s',
            booking.pk,
        )
        return {'status': 'error', 'reason': 'empty_output'}
    return {'status': 'ok', 'summary': summary}


def _record_failed_attempt(call, reason, *, now=None):
    """Count one not-ready attempt; terminal after the budget."""
    attempts = call.capture_attempts + 1
    if attempts >= CAPTURE_MAX_ATTEMPTS:
        return _mark_unavailable(call, reason, attempts=attempts, now=now)
    call.capture_attempts = attempts
    call.save(update_fields=['capture_attempts', 'updated_at'])
    logger.info(
        'Transcript not ready for call %s, attempt %d/%d (%s)',
        call.pk, attempts, CAPTURE_MAX_ATTEMPTS, reason,
    )
    return {'status': 'not_ready', 'attempts': attempts, 'reason': reason}


def _mark_unavailable(call, reason, attempts=None, *, now=None):
    """Record the terminal no-recording/no-transcript state."""
    call.capture_unavailable_at = now or timezone.now()
    if attempts is not None:
        call.capture_attempts = attempts
    call.save(update_fields=[
        'capture_unavailable_at', 'capture_attempts', 'updated_at',
    ])
    logger.warning(
        'Capture marked unavailable for call %s (%s)',
        call.pk, reason,
    )
    return {'status': 'unavailable', 'reason': reason}


def _extract_transcript_url(recording_files):
    """The ``audio_transcript`` download URL (same pick as events)."""
    for rec_file in recording_files:
        if rec_file.get('recording_type') == 'audio_transcript':
            return (rec_file.get('download_url') or '').strip()
    return ''


def _extract_share_url(data):
    """The durable share/play pointer; never a signed download URL.

    Zoom's top-level ``share_url`` does not expire; per-file
    ``download_url`` / ``play_url`` values are signed and do, so they
    are never stored.
    """
    return (data.get('share_url') or '').strip()


def _download_vtt(transcript_url):
    """Fetch the VTT document bytes with the OAuth token attached.

    Mirrors ``jobs.tasks.recording_transcript._download_vtt``: Zoom
    download URLs require the ``access_token`` query parameter.
    """
    import requests

    from integrations.services.zoom import get_access_token

    token = get_access_token()
    separator = '&' if '?' in transcript_url else '?'
    response = requests.get(
        f'{transcript_url}{separator}access_token={token}',
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def parse_transcript_vtt(raw):
    """Delegate to the shared VTT parser (single parsing contract)."""
    from jobs.tasks.recording_transcript import parse_vtt_to_text

    return parse_vtt_to_text(raw)


def get_meeting_recordings(booking):
    """Duck-typed pass-through: Booking carries ``zoom_meeting_id``.

    The shared helper only reads ``zoom_meeting_id`` off the object it
    is given, so a booking works as-is (issue #1951 keeps one helper).
    """
    from integrations.services.zoom import get_meeting_recordings as _list

    return _list(booking)
