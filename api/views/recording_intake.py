"""Staff-authenticated recording lifecycle intake, called by Dapier."""

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from events.services.recording_intake import (
    RecordingIdentityConflict,
    RecordingIntakeError,
    match_recording_event,
    receive_recording,
)

PHASES = {'recording.completed', 'recording.transcript_completed'}


def _timestamp(value):
    if not isinstance(value, str):
        return None
    result = parse_datetime(value)
    if result is None or timezone.is_naive(result):
        return None
    return result


def _validated(data):
    if not isinstance(data, dict) or data.get('event') not in PHASES:
        raise ValueError('event must identify a recording lifecycle stage')
    start = _timestamp(data.get('start_time'))
    available = _timestamp(data.get('available_at'))
    if available is None:
        if data.get('available_at') is not None:
            raise ValueError('available_at must be a timezone-aware timestamp')
        available = timezone.now()
    if start is None or available > timezone.now():
        raise ValueError('start_time and available_at must be valid timezone-aware timestamps')
    if not data.get('meeting_id') or not isinstance(data.get('recording_uuid'), str):
        raise ValueError('meeting_id and recording_uuid are required')
    if not data['recording_uuid'] or len(data['recording_uuid']) > 255:
        raise ValueError('recording_uuid must contain 1 to 255 characters')
    return start, available


@csrf_exempt
@token_required
@require_methods('POST')
@openapi_spec(
    tag='Events', summary='Receive a Zoom recording lifecycle notification',
    methods={'POST': {'summary': 'Receive a Zoom recording lifecycle notification'}},
)
def recording_intake(request):
    data, error = parse_json_body(request)
    if error is not None:
        return error
    try:
        start, available = _validated(data)
        event = match_recording_event(data['meeting_id'], start)
    except RecordingIntakeError:
        return error_response('More than one event matches this occurrence', 'ambiguous_occurrence', status=409)
    except ValueError as exc:
        return error_response(str(exc), 'invalid_recording_event', status=422)
    if event is None:
        return JsonResponse({'matched': False})
    try:
        result = receive_recording(
            event, phase=data['event'], recording_uuid=data['recording_uuid'], available_at=available,
        )
    except RecordingIdentityConflict:
        return error_response('A different recording occurrence is already bound to this event', 'conflicting_recording_identity', status=409)
    return JsonResponse(result, status=202)
