"""Scheduled recording pipeline watch (intake 1f596393).

Polls Zoom for finished MP4s so the S3 upload and the host email no longer
wait for ``recording.transcript_completed``, then alerts staff when the
recording-ready bound is missed. Logic lives in
:mod:`events.services.recording_pipeline_watch`.
"""


def watch_recording_pipeline():
    from events.services.recording_pipeline_watch import run_recording_pipeline_watch

    return run_recording_pipeline_watch()
