"""Authenticated source material for the draft-only Hub job; no storage URLs."""

from botocore.exceptions import BotoCoreError, ClientError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from events.models import Event
from jobs.tasks.recordings_s3 import (
    build_transcript_s3_key,
    get_recordings_s3_client,
    get_recordings_s3_config,
)


@token_required
@require_methods('GET')
@openapi_spec(tag='Events', methods={'GET': {'summary': 'Read timestamped transcript for a draft workshop job'}})
def recording_preparation_source(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not event.transcript_s3_url or not event.transcript_text:
        return error_response('Transcript capture has not finished', 'transcript_not_ready', status=409)
    config = get_recordings_s3_config()
    try:
        result = get_recordings_s3_client(config).get_object(
            Bucket=config.bucket, Key=build_transcript_s3_key(event),
        )
        raw = result['Body'].read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return error_response('Transcript exceeds preparation source size limit', 'transcript_too_large', status=413)
        transcript = raw.decode('utf-8-sig')
    except (BotoCoreError, ClientError, UnicodeError):
        return error_response('Transcript source is temporarily unavailable', 'transcript_unavailable', status=503)
    return JsonResponse({
        'event_id': event.pk, 'title': event.title,
        'start_datetime': event.start_datetime.isoformat(),
        'description': event.description,
        'transcript_vtt': transcript, 'transcript_text': event.transcript_text,
        'draft_only': True,
    })


@token_required
@require_methods('GET')
@openapi_spec(tag='Events', methods={'GET': {'summary': 'Read the Hub draft preparation receipt and progress'}})
def recording_preparation_status(request, slug):
    from events.models import RecordingPreparation

    event = get_object_or_404(Event, slug=slug)
    preparation = RecordingPreparation.objects.filter(event=event).first()
    if preparation is None:
        return JsonResponse({'status': 'not_requested', 'event_id': event.pk})
    return JsonResponse({
        'event_id': event.pk, 'status': preparation.status,
        'delivery_id': preparation.delivery_id,
        'ready_at': preparation.ready_at.isoformat(),
        'started_at': _iso(preparation.started_at),
        'finished_at': _iso(preparation.finished_at),
        'alerted_at': _iso(preparation.alerted_at),
        'error_code': preparation.error_code,
        'artifacts': preparation.artifacts,
    })


def _iso(value):
    if value is not None:
        return value.isoformat()
    return None
