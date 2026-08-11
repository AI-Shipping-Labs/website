"""Staff-token CRUD API for Call profiles (#1404)."""

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, JsonResponse
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
from community.models import CallHost
from studio.forms import CallProfileForm

CALL_PROFILE_FIELDS = {
    'name',
    'slug',
    'role_label',
    'photo_url',
    'booking_url',
    'is_active',
    'order',
}

_CALL_PROFILE_PROPERTIES = {
    'name': {'type': 'string'},
    'slug': {'type': 'string'},
    'role_label': {'type': 'string'},
    'photo_url': {'type': 'string', 'format': 'uri'},
    'booking_url': {'type': 'string', 'format': 'uri'},
    'is_active': {'type': 'boolean'},
    'order': {'type': 'integer', 'minimum': 0},
}

_CALL_PROFILE_RESPONSE_SCHEMA = {
    'type': 'object',
    'required': [
        'id', 'name', 'slug', 'role_label', 'photo_url', 'booking_url',
        'is_active', 'order', 'created_at', 'updated_at',
    ],
    'properties': {
        'id': {'type': 'integer'},
        **_CALL_PROFILE_PROPERTIES,
        'created_at': {'type': 'string', 'format': 'date-time'},
        'updated_at': {'type': 'string', 'format': 'date-time'},
    },
}

_ERROR_RESPONSE_SCHEMA = {'$ref': '#/components/schemas/ErrorResponse'}

_CALL_PROFILE_EXAMPLE = {
    'id': 1,
    'name': 'Jordan Lee',
    'slug': 'jordan-lee',
    'role_label': 'AI product coach',
    'photo_url': 'https://example.com/jordan.jpg',
    'booking_url': 'https://calendar.app.google/example',
    'is_active': True,
    'order': 10,
    'created_at': '2026-08-11T12:00:00+00:00',
    'updated_at': '2026-08-11T12:00:00+00:00',
}


def serialize_call_profile(profile):
    """Return only the public operator contract for a Call profile."""
    return {
        'id': profile.pk,
        'name': profile.name,
        'slug': profile.slug,
        'role_label': profile.role_label,
        'photo_url': profile.photo_url,
        'booking_url': profile.booking_url,
        'is_active': profile.is_active,
        'order': profile.order,
        'created_at': profile.created_at.isoformat(),
        'updated_at': profile.updated_at.isoformat(),
    }


def _form_error_details(form):
    details = {}
    for field, errors in form.errors.items():
        messages = [str(error) for error in errors]
        details[field] = messages[0] if len(messages) == 1 else messages
    return details


def _normalize_payload(data, *, existing=None):
    errors = {}
    for field in data:
        if field not in CALL_PROFILE_FIELDS:
            errors[field] = 'Unknown field.'

    if existing is None:
        values = {
            'name': '',
            'slug': '',
            'role_label': '',
            'photo_url': '',
            'booking_url': '',
            'is_active': True,
            'order': 0,
        }
    else:
        values = {
            field: getattr(existing, field)
            for field in CALL_PROFILE_FIELDS
        }

    for field, value in data.items():
        if field not in CALL_PROFILE_FIELDS:
            continue
        if field == 'is_active':
            if not isinstance(value, bool):
                errors[field] = 'Must be a boolean.'
            else:
                values[field] = value
        elif field == 'order':
            if isinstance(value, bool) or not isinstance(value, int):
                errors[field] = 'Must be an integer.'
            else:
                values[field] = value
        else:
            if not isinstance(value, str):
                errors[field] = 'Must be a string.'
            else:
                values[field] = value.strip()

    return values, errors


def _save_form(form):
    if not form.is_valid():
        return None, validation_response(_form_error_details(form))
    try:
        return form.save(), None
    except IntegrityError:
        return None, validation_response({
            'slug': 'A call profile with this slug already exists.',
        })


@token_required
@csrf_exempt
@require_methods('GET', 'POST')
@openapi_spec(
    tag='Call profiles',
    methods={
        'GET': {
            'summary': 'List Call profiles',
            'responses': {
                200: {
                    'description': 'Call profiles ordered by display order, then name.',
                    'example': {'call_profiles': [_CALL_PROFILE_EXAMPLE]},
                    'schema': {
                        'type': 'object',
                        'required': ['call_profiles'],
                        'properties': {
                            'call_profiles': {
                                'type': 'array',
                                'items': _CALL_PROFILE_RESPONSE_SCHEMA,
                            },
                        },
                    },
                },
                401: {
                    'description': 'Missing or invalid staff token.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
            },
        },
        'POST': {
            'summary': 'Create a Call profile',
            'request_body': {
                'required': ['name'],
                'properties': _CALL_PROFILE_PROPERTIES,
                'example': {
                    'name': 'Jordan Lee',
                    'slug': 'jordan-lee',
                    'booking_url': '',
                    'is_active': False,
                    'order': 10,
                },
            },
            'responses': {
                201: {
                    'description': 'Call profile created.',
                    'example': _CALL_PROFILE_EXAMPLE,
                    'schema': _CALL_PROFILE_RESPONSE_SCHEMA,
                },
                400: {
                    'description': 'Malformed JSON or a non-object body.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                401: {
                    'description': 'Missing or invalid staff token.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                422: {
                    'description': 'Validation error; no profile was created.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
            },
        },
    },
)
def call_profiles_collection(request):
    """GET/POST ``/api/call-profiles``."""
    if request.method == 'GET':
        profiles = CallHost.objects.order_by('order', 'name')
        return JsonResponse({
            'call_profiles': [serialize_call_profile(profile) for profile in profiles],
        })

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    values, errors = _normalize_payload(data)
    form = CallProfileForm(values)
    if not form.is_valid():
        for field, detail in _form_error_details(form).items():
            errors.setdefault(field, detail)
    if errors:
        return validation_response(errors)
    with transaction.atomic():
        profile, save_error = _save_form(form)
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_call_profile(profile), status=201)


@token_required
@csrf_exempt
@require_methods('GET', 'PATCH', 'DELETE')
@openapi_spec(
    tag='Call profiles',
    methods={
        'GET': {
            'summary': 'Retrieve a Call profile',
            'responses': {
                200: {
                    'description': 'Call profile.',
                    'example': _CALL_PROFILE_EXAMPLE,
                    'schema': _CALL_PROFILE_RESPONSE_SCHEMA,
                },
                401: {
                    'description': 'Missing or invalid staff token.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                404: {
                    'description': 'Call profile not found.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
            },
        },
        'PATCH': {
            'summary': 'Update a Call profile',
            'description': (
                'Partially updates a Call profile. An active profile must have an '
                'HTTP(S) booking URL; hide it in the same request before clearing '
                'its booking URL.'
            ),
            'request_body': {
                'properties': _CALL_PROFILE_PROPERTIES,
                'example': {
                    'booking_url': 'https://calendar.app.google/example',
                    'is_active': True,
                },
            },
            'responses': {
                200: {
                    'description': 'Call profile updated.',
                    'example': _CALL_PROFILE_EXAMPLE,
                    'schema': _CALL_PROFILE_RESPONSE_SCHEMA,
                },
                400: {
                    'description': 'Malformed JSON or a non-object body.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                401: {
                    'description': 'Missing or invalid staff token.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                404: {
                    'description': 'Call profile not found.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                422: {
                    'description': 'Validation error; no fields were changed.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
            },
        },
        'DELETE': {
            'summary': 'Delete an unused Call profile',
            'description': (
                'Deletes only profiles without booked-call history. Hide an in-use '
                'profile with PATCH is_active=false instead.'
            ),
            'responses': {
                204: {'description': 'Unused Call profile deleted.'},
                401: {
                    'description': 'Missing or invalid staff token.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                404: {
                    'description': 'Call profile not found.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                },
                409: {
                    'description': 'Call profile has booked-call history.',
                    'schema': _ERROR_RESPONSE_SCHEMA,
                    'example': {
                        'error': (
                            "This call profile has booked-call history and can't be "
                            'deleted. Hide it with PATCH is_active=false instead.'
                        ),
                        'code': 'call_profile_in_use',
                    },
                },
            },
        },
    },
)
def call_profile_detail(request, slug):
    """GET/PATCH/DELETE ``/api/call-profiles/<slug>``."""
    if request.method == 'DELETE':
        with transaction.atomic():
            profile = CallHost.objects.select_for_update().filter(slug=slug).first()
            if profile is None:
                return error_response(
                    'Call profile not found',
                    'call_profile_not_found',
                    status=404,
                )
            if profile.booked_calls.exists():
                return error_response(
                    "This call profile has booked-call history and can't be deleted. "
                    'Hide it with PATCH is_active=false instead.',
                    'call_profile_in_use',
                    status=409,
                )
            try:
                profile.delete()
            except ProtectedError:
                return error_response(
                    "This call profile has booked-call history and can't be deleted. "
                    'Hide it with PATCH is_active=false instead.',
                    'call_profile_in_use',
                    status=409,
                )
        return HttpResponse(status=204)

    profile = CallHost.objects.filter(slug=slug).first()
    if profile is None:
        return error_response(
            'Call profile not found',
            'call_profile_not_found',
            status=404,
        )
    if request.method == 'GET':
        return JsonResponse(serialize_call_profile(profile))

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    values, errors = _normalize_payload(data, existing=profile)
    if errors:
        return validation_response(errors)

    with transaction.atomic():
        locked_profile = CallHost.objects.select_for_update().get(pk=profile.pk)
        values, errors = _normalize_payload(data, existing=locked_profile)
        if errors:
            return validation_response(errors)
        updated, save_error = _save_form(
            CallProfileForm(values, instance=locked_profile),
        )
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_call_profile(updated))
