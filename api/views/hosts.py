"""Staff token API for event host profiles (#1031)."""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    body_must_be_object_response,
    coerce_optional_text,
    parse_json_body,
    require_methods,
    validation_response,
)
from events.models import Host

HOST_MUTABLE_FIELDS = {
    'name',
    'slug',
    'title',
    'bio',
    'photo_url',
    'email',
    'is_active',
}

_HOST_EXAMPLE = {
    'id': 1,
    'name': 'Alexey Grigorev',
    'slug': 'alexey-grigorev',
    'title': 'Chief Agent Officer at AI Shipping Labs',
    'bio': 'Software engineer and machine learning practitioner.',
    'bio_html': '<p>Software engineer and machine learning practitioner.</p>',
    'photo_url': '',
    'email': 'alexey@aishippinglabs.com',
    'is_active': True,
    'created_at': '2026-06-16T12:00:00+00:00',
    'updated_at': '2026-06-17T12:00:00+00:00',
}


def _iso(value):
    return value.isoformat() if value is not None else None


def serialize_host_profile(host):
    """Return the canonical staff host profile object."""
    return {
        'id': host.id,
        'name': host.name,
        'slug': host.slug,
        'title': host.title,
        'bio': host.bio,
        'bio_html': host.bio_html,
        'photo_url': host.photo_url,
        'email': host.email,
        'is_active': host.is_active,
        'created_at': _iso(host.created_at),
        'updated_at': _iso(host.updated_at),
    }


def _collect_host_values(data, *, existing):
    errors = {}
    values = {}

    for field in sorted(data):
        if field not in HOST_MUTABLE_FIELDS:
            errors[field] = 'Unknown field.'

    if 'name' in data:
        values['name'] = coerce_optional_text(data['name'])
        if not values['name']:
            errors['name'] = 'Name is required.'

    if 'slug' in data:
        values['slug'] = coerce_optional_text(data['slug'])
        if not values['slug']:
            errors['slug'] = 'Slug is required.'
        else:
            duplicate_qs = Host.objects.filter(slug=values['slug'])
            duplicate_qs = duplicate_qs.exclude(pk=existing.pk)
            if duplicate_qs.exists():
                errors['slug'] = 'Slug already in use.'

    for field in ('title', 'photo_url', 'email'):
        if field in data:
            values[field] = coerce_optional_text(data[field])

    if 'bio' in data:
        values['bio'] = '' if data['bio'] is None else str(data['bio'])

    if 'is_active' in data:
        if not isinstance(data['is_active'], bool):
            errors['is_active'] = 'Must be a boolean.'
        values['is_active'] = data['is_active']

    if values.get('email'):
        try:
            validate_email(values['email'])
        except ValidationError:
            errors['email'] = 'Must be a valid email address.'

    for field in ('name', 'slug', 'title'):
        if field in values:
            max_length = Host._meta.get_field(field).max_length
            if max_length is not None and len(values[field]) > max_length:
                errors[field] = f'Must be {max_length} characters or fewer.'

    if 'photo_url' in values:
        max_length = Host._meta.get_field('photo_url').max_length
        if len(values['photo_url']) > max_length:
            errors['photo_url'] = f'Must be {max_length} characters or fewer.'

    return values, errors


def _save_host_or_error(host):
    try:
        host.full_clean()
        host.save()
    except ValidationError as exc:
        details = exc.message_dict if hasattr(exc, 'message_dict') else {
            'host': exc.messages,
        }
        return validation_response(details)
    except IntegrityError:
        return validation_response({'slug': 'Slug already in use.'})
    return None


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Hosts',
    summary='List host profiles',
    methods={
        'GET': {
            'summary': 'List host profiles',
            'responses': {
                200: {
                    'description': 'Host profiles ordered by name.',
                    'example': {'hosts': [_HOST_EXAMPLE]},
                },
                401: {'description': 'Missing or invalid staff token.'},
            },
        },
    },
)
def hosts_collection(request):
    """GET ``/api/hosts``."""
    hosts = Host.objects.order_by('name')
    return JsonResponse(
        {'hosts': [serialize_host_profile(host) for host in hosts]},
        status=200,
    )


@token_required
@csrf_exempt
@require_methods('GET', 'PATCH')
@openapi_spec(
    tag='Hosts',
    summary='Retrieve or update a host profile',
    methods={
        'GET': {
            'summary': 'Retrieve a host profile',
            'responses': {
                200: {
                    'description': 'Host profile.',
                    'example': _HOST_EXAMPLE,
                },
                404: {
                    'description': 'Host not found.',
                    'example': {
                        'error': 'Host not found',
                        'code': 'unknown_host',
                    },
                },
            },
        },
        'PATCH': {
            'summary': 'Update a host profile',
            'description': (
                'Updates display/profile fields only. Host email and title '
                'are not used for calendar invite recipient resolution.'
            ),
            'request_body': {
                'properties': {
                    'name': {'type': 'string'},
                    'slug': {'type': 'string'},
                    'title': {'type': 'string'},
                    'bio': {'type': 'string'},
                    'photo_url': {'type': 'string', 'format': 'uri'},
                    'email': {'type': 'string', 'format': 'email'},
                    'is_active': {'type': 'boolean'},
                },
                'example': {
                    'title': 'Chief Agent Officer at AI Shipping Labs',
                },
            },
            'responses': {
                200: {
                    'description': 'Host profile updated.',
                    'example': _HOST_EXAMPLE,
                },
                400: {'description': 'Invalid JSON body.'},
                404: {'description': 'Host not found.'},
                422: {
                    'description': (
                        'Validation error, including duplicate slug or '
                        'invalid email.'
                    ),
                },
            },
        },
    },
)
def host_detail(request, slug):
    """GET/PATCH ``/api/hosts/<slug>``."""
    host = Host.objects.filter(slug=slug).first()
    if host is None:
        return error_response(
            'Host not found',
            'unknown_host',
            status=404,
        )

    if request.method == 'GET':
        return JsonResponse(serialize_host_profile(host), status=200)

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    values, errors = _collect_host_values(data, existing=host)
    if errors:
        return validation_response(errors)

    with transaction.atomic():
        for field, value in values.items():
            setattr(host, field, value)
        save_error = _save_host_or_error(host)
        if save_error is not None:
            return save_error

    return JsonResponse(serialize_host_profile(host), status=200)
