"""Deterministic Dapier intake; MP4 processing never waits for a transcript."""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import quote

from django.db import transaction

from events.models import Event, RecordingPreparation
from events.services.recording_transcript import refresh_transcript_from_zoom


class RecordingIntakeError(ValueError):
    pass


def match_recording_event(meeting_id, start):
    candidates = list(Event.objects.filter(
        zoom_meeting_id=str(meeting_id),
        start_datetime__gte=start - timedelta(hours=3),
        start_datetime__lte=start + timedelta(hours=3),
    ).exclude(status='cancelled')[:2])
    if len(candidates) > 1:
        raise RecordingIntakeError('ambiguous_occurrence')
    if candidates:
        return candidates[0]
    return None


class RecordingIdentityConflict(RecordingIntakeError):
    pass


def receive_recording(event, *, phase, recording_uuid, available_at):
    """Persist availability before contacting providers, then enqueue work."""
    try:
        event = _record_receipt(event.pk, phase, recording_uuid, available_at)
    except RecordingIdentityConflict:
        from events.services.recording_pipeline_alerts import deliver_pipeline_alert

        deliver_pipeline_alert(
            event, f'Conflicting recording identity (event {event.pk})',
            'A recording lifecycle notification names a different Zoom occurrence '
            'from this event canonical recording. Intake was rejected without '
            'overwriting recording or transcript state. Review the event mapping.',
        )
        raise
    from jobs.tasks import async_task

    if phase == 'recording.transcript_completed':
        async_task('events.services.recording_preparation.dispatch_preparation', event.pk)

    async_task(
        'events.services.recording_intake.process_recording',
        event.pk, phase, recording_uuid,
    )
    return {'matched': True, 'event_id': event.pk, 'stage': phase}


def _mark_video_available(event, available_at):
    if event.recording_zoom_available_at is None or available_at < event.recording_zoom_available_at:
        event.recording_zoom_available_at = available_at
        event.save(update_fields=['recording_zoom_available_at', 'updated_at'])


def process_recording(event_id, phase, recording_uuid):
    event = Event.objects.get(pk=event_id)
    instance = SimpleNamespace(zoom_meeting_id=quote(quote(recording_uuid, safe=''), safe=''))
    from integrations.services.zoom import get_meeting_recordings

    data = get_meeting_recordings(instance)
    refresh_transcript_from_zoom(event, source='Zoom recording intake', data=data)
    if phase != 'recording.transcript_completed':
        return
    from events.services.recording_transcript import enqueue_recording_transcript_task

    enqueue_recording_transcript_task(event, source='Zoom transcript intake')


def _mark_transcript_ready(event, available_at, recording_uuid):
    preparation, created = RecordingPreparation.objects.get_or_create(
        event=event,
        defaults={'ready_at': available_at, 'recording_uuid': recording_uuid},
    )
    if not created:
        preparation.ready_at = min(preparation.ready_at, available_at)
        if not preparation.recording_uuid:
            preparation.recording_uuid = recording_uuid
        preparation.save(update_fields=['ready_at', 'recording_uuid', 'updated_at'])


@transaction.atomic
def _record_receipt(event_id, phase, recording_uuid, available_at):
    event = Event.objects.select_for_update().get(pk=event_id)
    if event.recording_zoom_uuid and event.recording_zoom_uuid != recording_uuid:
        raise RecordingIdentityConflict('conflicting_recording_identity')
    if not event.recording_zoom_uuid:
        event.recording_zoom_uuid = recording_uuid
        event.save(update_fields=['recording_zoom_uuid', 'updated_at'])
    if phase == 'recording.completed':
        _mark_video_available(event, available_at)
    else:
        _mark_transcript_ready(event, available_at, recording_uuid)
    return event
