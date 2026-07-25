"""Staff-token instructor account-linking API (issue #1345).

Operator/automation surface for reconciling instructor rows against verified
platform accounts so the #1341 ``content_comment`` notifications reach content
authors. Follows the ``@token_required`` + ``@openapi_spec`` pattern used by
``api/views/course_instructors.py``.
"""

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import (
    body_must_be_object_response,
    parse_bool_query,
    parse_json_body,
    require_methods,
    validation_response,
)
from content.models import Instructor
from content.services.instructor_linking import (
    comment_content_count,
    find_matching_user,
    reconcile_instructors,
)

User = get_user_model()


def _serialize_instructor(instructor):
    return {
        'instructor_id': instructor.instructor_id,
        'name': instructor.name,
        'email': instructor.email,
        'linked': instructor.user_id is not None,
        'linked_user_email': (
            instructor.user.email if instructor.user_id is not None else None
        ),
        'comment_content_count': comment_content_count(instructor),
    }


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Instructors',
    methods={
        'GET': {
            'summary': 'List instructors with account-link state',
            'description': (
                'Lists instructors and whether each is linked to a platform '
                'account, plus the count of comment-bearing content items '
                'attributed to the instructor. Pass ``?linked=false`` to '
                'return only unlinked rows.'
            ),
            'query': {
                'linked': {
                    'type': 'boolean',
                    'required': False,
                    'description': (
                        'When ``false``, return only instructors with no '
                        'linked account.'
                    ),
                },
            },
            'responses': {
                200: {
                    'description': 'Instructors with link state.',
                    'example': {
                        'instructors': [
                            {
                                'instructor_id': 'ada',
                                'name': 'Ada',
                                'email': 'ada@test.com',
                                'linked': True,
                                'linked_user_email': 'ada@test.com',
                                'comment_content_count': 2,
                            }
                        ]
                    },
                },
                401: {'description': 'Missing or invalid staff token.'},
            },
        },
    },
)
def instructors_collection(request):
    qs = Instructor.objects.select_related('user').order_by('name')
    linked_filter = parse_bool_query(request.GET.get('linked'))
    if linked_filter is False:
        qs = qs.filter(user__isnull=True)
    elif linked_filter is True:
        qs = qs.filter(user__isnull=False)
    return JsonResponse(
        {'instructors': [_serialize_instructor(i) for i in qs]}
    )


@token_required
@csrf_exempt
@require_methods('PUT')
@openapi_spec(
    tag='Instructors',
    methods={
        'PUT': {
            'summary': 'Set or clear an instructor account link',
            'description': (
                'Sets ``Instructor.user`` by ``email`` (verified accounts '
                'only) or ``user_id``, or clears it with ``{"user": null}``. '
                'An operator override set here is honored by the auto-link '
                'rule, which never overwrites a non-null link.'
            ),
            'path_params': {
                'instructor_id': {'type': 'string', 'required': True},
            },
            'request_body': {
                'type': 'object',
                'properties': {
                    'email': {'type': 'string'},
                    'user_id': {'type': 'integer'},
                    'user': {'type': 'null'},
                },
                'example': {'email': 'ada@test.com'},
            },
            'responses': {
                200: {
                    'description': 'Updated link state.',
                    'example': {
                        'instructor_id': 'ada',
                        'name': 'Ada',
                        'email': 'ada@test.com',
                        'linked': True,
                        'linked_user_email': 'ada@test.com',
                        'comment_content_count': 0,
                    },
                },
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Instructor not found.'},
                422: {
                    'description': (
                        'No verified account matched, unknown user_id, or a '
                        'malformed body.'
                    )
                },
            },
        },
    },
)
def instructor_user(request, instructor_id):
    instructor = get_object_or_404(Instructor, instructor_id=instructor_id)

    data, parse_error = parse_json_body(request)
    if parse_error:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    # Clear: {"user": null}.
    if 'user' in data:
        if data['user'] is not None:
            return validation_response(
                {'user': 'Only null is accepted to clear the link.'}
            )
        instructor.user = None
        instructor.save(update_fields=['user'])
        return JsonResponse(_serialize_instructor(instructor))

    if 'email' in data:
        email = str(data.get('email') or '').strip()
        if not email:
            return validation_response({'email': 'This field cannot be empty.'})
        match = find_matching_user(email)
        if match is None:
            return validation_response(
                {'email': 'No verified account matches this email.'}
            )
        instructor.user = match
        instructor.save(update_fields=['user'])
        return JsonResponse(_serialize_instructor(instructor))

    if 'user_id' in data:
        try:
            user_id = int(data['user_id'])
        except (TypeError, ValueError):
            return validation_response(
                {'user_id': 'Must be an integer user id.'}
            )
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return validation_response(
                {'user_id': 'No account with this id.'}
            )
        instructor.user = user
        instructor.save(update_fields=['user'])
        return JsonResponse(_serialize_instructor(instructor))

    return validation_response(
        {'body': 'Provide "email", "user_id", or {"user": null}.'}
    )


@token_required
@csrf_exempt
@require_methods('POST')
@openapi_spec(
    tag='Instructors',
    methods={
        'POST': {
            'summary': 'Reconcile instructor account links',
            'description': (
                'Runs the same auto-link reconciliation as the '
                '``link_instructors`` management command: fills every NULL '
                'link that has a verified email match, never overwriting an '
                'operator override. Returns summary counts.'
            ),
            'responses': {
                200: {
                    'description': 'Reconciliation summary counts.',
                    'example': {
                        'scanned': 2,
                        'newly_linked': 1,
                        'left_unlinked': 1,
                    },
                },
                401: {'description': 'Missing or invalid staff token.'},
            },
        },
    },
)
def instructors_reconcile(request):
    summary = reconcile_instructors()
    return JsonResponse(summary)


__all__ = [
    'instructors_collection',
    'instructor_user',
    'instructors_reconcile',
]
