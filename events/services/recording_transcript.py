"""Derived status, enqueue, and Zoom re-list helpers for transcripts.

Issue #1597. Mirrors :mod:`events.services.recording_upload`: the webhook
(or an operator surface) enqueues the django-q task through the helper here,
and the task itself lives in
:mod:`jobs.tasks.recording_transcript`. The VTT is independent of the MP4,
so the transcript task runs in parallel with the upload task.

Retry model: the transcript task counts failed fetch attempts on
``Event.transcript_fetch_attempts``. Zoom can deliver ``recording.completed``
before the VTT is processed, so not-ready fetches retry with backoff; once
``TRANSCRIPT_MAX_ATTEMPTS`` is reached without a transcript the event is
marked unavailable (``transcript_unavailable_at``) and recap drafting is
skipped. A later ``recording.transcript.completed`` webhook clears the
marker, so a transcript that shows up late is still captured.
"""

TRANSCRIPT_STATUS_NONE = 'none'
TRANSCRIPT_STATUS_WAITING = 'waiting'
TRANSCRIPT_STATUS_STORED = 'stored'
TRANSCRIPT_STATUS_UNAVAILABLE = 'unavailable'

# 1 initial attempt + 3 django-q retries, mirroring the upload task's
# max_retries=3 contract (django-q2 max_attempts = max_retries + 1).
TRANSCRIPT_MAX_RETRIES = 3
TRANSCRIPT_MAX_ATTEMPTS = TRANSCRIPT_MAX_RETRIES + 1

# The VTT is small (tens of KB), so the task needs a fraction of the MP4
# upload's timeout budget. Same shape as the upload constants so the enqueue
# contract stays uniform.
TRANSCRIPT_TASK_TIMEOUT_SECONDS = 300
TRANSCRIPT_TASK_RETRY_SECONDS = 900


def transcript_status(event):
    """Return the derived operator status for transcript capture.

    - ``stored``: plain-text transcript is on the event.
    - ``unavailable``: Zoom has no transcript for this meeting (retries
      exhausted, or no transcript URL ever arrived and the task marked it).
    - ``waiting``: a transcript URL is stored but the text is not yet —
      the task is pending, retrying, or a run can be re-enqueued.
    - ``none``: nothing known about a transcript yet.
    """
    if event.transcript_text:
        return TRANSCRIPT_STATUS_STORED
    if event.transcript_unavailable_at is not None:
        return TRANSCRIPT_STATUS_UNAVAILABLE
    if event.transcript_url:
        return TRANSCRIPT_STATUS_WAITING
    return TRANSCRIPT_STATUS_NONE


def transcript_capture_needed(event):
    """True when the transcript task should run for this event.

    Idempotency guard shared by the webhook and the operator sync surfaces:
    a stored transcript URL with no parsed text yet, and not already
    marked unavailable.
    """
    return bool(
        event.transcript_url
        and not event.transcript_text
        and event.transcript_unavailable_at is None
    )


def mark_transcript_unavailable(event, *, attempts=None):
    """Record the terminal no-transcript-available state (idempotent)."""
    from django.utils import timezone

    update_fields = []
    if attempts is not None and event.transcript_fetch_attempts != attempts:
        event.transcript_fetch_attempts = attempts
        update_fields.append('transcript_fetch_attempts')
    if event.transcript_unavailable_at is None:
        event.transcript_unavailable_at = timezone.now()
        update_fields.append('transcript_unavailable_at')
    if update_fields:
        update_fields.append('updated_at')
        event.save(update_fields=update_fields)
    return event


def clear_transcript_unavailable(event):
    """Forget the unavailable marker so a late transcript can be captured."""
    if event.transcript_unavailable_at is not None:
        event.transcript_unavailable_at = None
        event.transcript_fetch_attempts = 0
        event.save(update_fields=[
            'transcript_unavailable_at',
            'transcript_fetch_attempts',
            'updated_at',
        ])
    return event


def enqueue_recording_transcript_task(event, *, source, redraft=False):
    """Enqueue ``transcribe_recording`` with transcript-specific timeouts.

    The VTT URL is read from ``event.transcript_url`` inside the task, so a
    re-enqueue never needs the original queue args. Returns the django-q
    task id.
    """
    from jobs.tasks import async_task, build_task_name
    from jobs.tasks.recording_transcript import (
        TRANSCRIPT_MAX_RETRIES,
        TRANSCRIPT_TASK_RETRY_SECONDS,
        TRANSCRIPT_TASK_TIMEOUT_SECONDS,
    )

    return async_task(
        'jobs.tasks.recording_transcript.transcribe_recording',
        event.id,
        redraft,
        max_retries=TRANSCRIPT_MAX_RETRIES,
        retry_backoff=TRANSCRIPT_TASK_RETRY_SECONDS,
        timeout=TRANSCRIPT_TASK_TIMEOUT_SECONDS,
        task_name=build_task_name(
            'Transcribe event recording',
            f'event #{event.id} {event.title}',
            source,
        ),
    )


def refresh_transcript_from_zoom(event):
    """Re-list the meeting's recordings via the Zoom API (issue #1597).

    Picks up a transcript VTT — and the MP4 download URL — that the
    ``recording.completed`` webhook missed, for events that completed with
    an idle recording pipeline. Writes:

    - ``transcript_url``: the ``audio_transcript`` download URL, refreshed
      whenever Zoom reports one and the text is not stored yet (freshly
      signed URLs beat stale ones). A URL arriving for an event marked
      unavailable also clears that marker and the attempt counter.
    - ``recording_zoom_download_url`` + upload enqueue: only when the event
      has no S3 recording and no stored download URL, mirroring the
      webhook's duplicate-upload guard.

    Returns a dict describing what was refreshed. Raises ``ZoomAPIError``
    when the Zoom listing fails; callers decide whether that is fatal.
    """
    from django.utils import timezone

    from events.services.recording_upload import (
        enqueue_recording_upload_task,
        recording_upload_lease_is_active,
    )
    from integrations.services.zoom import get_meeting_recordings

    if not event.zoom_meeting_id:
        return {'refreshed': False, 'reason': 'no_meeting_id'}

    data = get_meeting_recordings(event)
    recording_files = data.get('recording_files', [])
    transcript_url = _extract_transcript_url(recording_files)
    download_url = _extract_video_download_url(recording_files)

    update_fields = []
    refreshed = {'refreshed': True, 'transcript_url': False,
                 'recording_download_url': False, 'upload_queued': False}

    if transcript_url and not event.transcript_text:
        if transcript_url != event.transcript_url:
            event.transcript_url = transcript_url
            update_fields.append('transcript_url')
            refreshed['transcript_url'] = True
        if event.transcript_unavailable_at is not None:
            event.transcript_unavailable_at = None
            event.transcript_fetch_attempts = 0
            update_fields.extend([
                'transcript_unavailable_at',
                'transcript_fetch_attempts',
            ])

    if (
        download_url
        and not event.recording_s3_url
        and not event.recording_zoom_download_url
    ):
        event.recording_zoom_download_url = download_url
        update_fields.append('recording_zoom_download_url')
        refreshed['recording_download_url'] = True
        if not recording_upload_lease_is_active(event):
            enqueue_recording_upload_task(event, download_url, source='Transcript sync')
            event.recording_upload_enqueued_at = timezone.now()
            update_fields.append('recording_upload_enqueued_at')
            refreshed['upload_queued'] = True

    if update_fields:
        update_fields.append('updated_at')
        event.save(update_fields=update_fields)
    return refreshed


def _extract_transcript_url(recording_files):
    """Return the ``audio_transcript`` download URL from recording files."""
    for rec_file in recording_files:
        if rec_file.get('recording_type') == 'audio_transcript':
            return (rec_file.get('download_url') or '').strip()
    return ''


def _extract_video_download_url(recording_files):
    """Return a usable MP4 download URL, preferring canonical view types.

    Same preference order as the webhook's video selection so a re-list
    picks the same file the webhook would have.
    """
    preferred = (
        'shared_screen_with_speaker_view',
        'shared_screen',
        'active_speaker',
    )
    for recording_type in preferred:
        for rec_file in recording_files:
            if rec_file.get('recording_type') != recording_type:
                continue
            download_url = (rec_file.get('download_url') or '').strip()
            if download_url:
                return download_url
    return ''
