"""ID-based staff API plumbing for event guest invitations."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    body_must_be_object_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from api.views.events import serialize_event
from events.models import Event
from events.services.guest_invitation import (
    GuestInvitationError,
    get_guest_invitation,
    invite_guest,
    preview_guest_invitation,
)

_INVITATION_EXAMPLE = {
    'event_id': 49,
    'event_slug': 'setting-up-a-remote-environment-for-agentic-workloads',
    'event_title': 'Setting up a remote environment for agentic workloads',
    'guest_email': 'guest@example.com',
    'registration_id': 123,
    'registration_status': 'registered',
    'email_status': 'sent',
    'dry_run': False,
    'attempt_count': 1,
}


def _event_or_error(event_id):
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        return None, error_response(
            'Event not found', 'unknown_event', status=404,
        )
    return event, None


def _invitation_error(exc):
    return error_response(
        exc.message, exc.code, status=exc.status, details=exc.details,
    )


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Events',
    methods={'GET': {
        'summary': 'Retrieve an event by numeric ID',
        'responses': {
            200: {'description': 'Authoritative event identity.'},
            404: {'description': 'Event not found.'},
        },
    }},
)
def event_by_id(request, event_id):
    event, response = _event_or_error(event_id)
    if response is not None:
        return response
    return JsonResponse(serialize_event(event))


@token_required
@csrf_exempt
@require_methods('POST')
@openapi_spec(
    tag='Events',
    methods={'POST': {
        'summary': 'Invite one guest to one event session',
        'description': (
            'Creates or reuses one event-specific registration and sends the '
            'ordinary attendee invitation. It never creates a standing series '
            'registration or host access. Set dry_run=true to validate without '
            'creating a user, registration, delivery, or email log.'
        ),
        'request_body': {
            'required': ['email'],
            'properties': {
                'email': {'type': 'string', 'format': 'email'},
                'dry_run': {'type': 'boolean', 'default': False},
            },
            'example': {'email': 'guest@example.com'},
        },
        'responses': {
            200: {'description': 'Existing or dry-run invitation state.', 'example': _INVITATION_EXAMPLE},
            201: {'description': 'Registration created and invitation attempted.', 'example': _INVITATION_EXAMPLE},
            404: {'description': 'Event not found.'},
            409: {'description': 'Event, alias, account, or host conflict.'},
            422: {'description': 'Invalid request body or guest email.'},
        },
    }},
)
def event_guest_invitations(request, event_id):
    event, response = _event_or_error(event_id)
    if response is not None:
        return response
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response(status=422)
    unexpected = sorted(set(data) - {'email', 'dry_run'})
    if unexpected:
        return validation_response({'unexpected_fields': unexpected})
    if not isinstance(data.get('dry_run', False), bool):
        return validation_response({'dry_run': 'Must be a boolean.'})
    try:
        result = (
            preview_guest_invitation(event, data.get('email'))
            if data.get('dry_run', False)
            else invite_guest(event, data.get('email'))
        )
    except GuestInvitationError as exc:
        return _invitation_error(exc)
    status = 201 if result['registration_status'] == 'registered' else 200
    return JsonResponse(result, status=status)


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Events',
    methods={'GET': {
        'summary': 'Read back one event guest invitation',
        'responses': {
            200: {'description': 'Authoritative invitation state.', 'example': _INVITATION_EXAMPLE},
            404: {'description': 'Event or guest invitation not found.'},
        },
    }},
)
def event_guest_invitation_detail(request, event_id, registration_id):
    event, response = _event_or_error(event_id)
    if response is not None:
        return response
    try:
        result = get_guest_invitation(event, registration_id)
    except GuestInvitationError as exc:
        return _invitation_error(exc)
    return JsonResponse(result)
