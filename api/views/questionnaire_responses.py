"""Staff-token API parity for the questionnaire response queue (#1289)."""

import json

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.onboarding import serialize_response, serialize_response_summary
from api.utils import require_methods
from api.views.onboarding import _parse_offset, _resolve_persona
from api.views.users import _parse_limit, _parse_since
from questionnaires.models import Response
from questionnaires.response_workflows import (
    VALID_PURPOSES,
    VALID_RESPONSE_STATUSES,
    VALID_REVIEW_FILTERS,
    ResponseNotSubmitted,
    compact_response_queryset,
    response_queryset,
    transition_response_review,
)


def _validation(field, value, allowed=None):
    details = {'field': field, 'value': value}
    if allowed is not None:
        details['allowed'] = list(allowed)
    return error_response(
        f'Invalid {field}: {value!r}', 'validation_error', status=422,
        details=details,
    )


def _collection_filters(request):
    status = request.GET.get('status') or 'submitted'
    review = request.GET.get('review') or 'awaiting'
    purpose = request.GET.get('purpose') or 'all'
    if status not in VALID_RESPONSE_STATUSES:
        return None, _validation('status', status, VALID_RESPONSE_STATUSES)
    if review not in VALID_REVIEW_FILTERS:
        return None, _validation('review', review, VALID_REVIEW_FILTERS)
    if purpose not in VALID_PURPOSES:
        return None, _validation('purpose', purpose, VALID_PURPOSES)
    raw_questionnaire = request.GET.get('questionnaire') or ''
    if raw_questionnaire:
        try:
            questionnaire = int(raw_questionnaire)
        except (TypeError, ValueError):
            return None, _validation('questionnaire', raw_questionnaire)
        if questionnaire < 1:
            return None, _validation('questionnaire', raw_questionnaire)
    else:
        questionnaire = None
    return {
        'status': status,
        'review': review,
        'purpose': purpose,
        'questionnaire': questionnaire,
        'search': (request.GET.get('q') or '').strip(),
    }, None


_SUMMARY_EXAMPLE = {
    'id': 84,
    'user_id': 12,
    'email': 'alex@example.com',
    'studio_user_url': '/studio/users/12/',
    'studio_response_url': '/studio/questionnaires/5/responses/84/',
    'questionnaire': {
        'id': 5,
        'slug': 'onboarding-engineer',
        'title': 'Engineer onboarding',
        'purpose': 'onboarding',
    },
    'status': 'submitted',
    'submitted_at': '2026-05-19T08:30:00+00:00',
    'updated_at': '2026-05-19T08:30:00+00:00',
    'answered_count': 4,
    'reviewed_at': None,
    'reviewed_by': None,
    'review_state': 'awaiting',
    'crm_record': None,
}


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Questionnaire responses',
    summary='List questionnaire responses awaiting operator review',
    methods={
        'GET': {
            'summary': 'List all-purpose questionnaire responses',
            'description': (
                'Staff-token queue. Defaults to submitted, awaiting review, '
                'newest first. Count is computed before limit/offset slicing.'
            ),
            'query': {
                'status': {'type': 'string', 'required': False},
                'review': {'type': 'string', 'required': False},
                'purpose': {'type': 'string', 'required': False},
                'questionnaire': {'type': 'integer', 'required': False},
                'q': {'type': 'string', 'required': False},
                'since': {'type': 'string', 'required': False},
                'limit': {'type': 'integer', 'required': False},
                'offset': {'type': 'integer', 'required': False},
            },
            'responses': {
                200: {'description': 'Response summaries.', 'example': {
                    'responses': [_SUMMARY_EXAMPLE], 'count': 1,
                    'limit': 50, 'offset': 0,
                }},
                401: {'description': 'Missing or invalid staff token.'},
                422: {'description': 'Invalid filter.'},
            },
        },
    },
)
def questionnaire_responses_collection(request):
    filters, error = _collection_filters(request)
    if error is not None:
        return error
    limit, error = _parse_limit(request.GET.get('limit'))
    if error is not None:
        return error
    offset, error = _parse_offset(request.GET.get('offset'))
    if error is not None:
        return error
    since, error = _parse_since(request.GET.get('since'))
    if error is not None:
        return error

    queryset = compact_response_queryset(**filters)
    if since is not None:
        if filters['status'] == 'draft':
            queryset = queryset.filter(updated_at__gte=since)
        elif filters['status'] == 'submitted':
            queryset = queryset.filter(submitted_at__gte=since)
        else:
            queryset = queryset.filter(
                Q(status='submitted', submitted_at__gte=since)
                | Q(status='draft', updated_at__gte=since)
            )
    count = queryset.count()
    rows = [
        serialize_response_summary(response)
        for response in queryset[offset:offset + limit]
    ]
    return JsonResponse({
        'responses': rows, 'count': count, 'limit': limit, 'offset': offset,
    })


def _full_response_payload(response):
    persona = None
    if response.questionnaire.purpose == 'onboarding':
        persona = _resolve_persona(response.questionnaire)
    return serialize_response(response, persona=persona)


@token_required
@csrf_exempt
@require_methods('GET', 'PATCH')
@openapi_spec(
    tag='Questionnaire responses',
    summary='Read or update one questionnaire response review state',
    methods={
        'GET': {
            'summary': 'Get a full questionnaire response snapshot',
            'responses': {
                200: {'description': 'Full shared response payload.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Response not found.'},
            },
        },
        'PATCH': {
            'summary': 'Review or reopen a submitted response',
            'request': {
                'content_type': 'application/json',
                'example': {'reviewed': True},
            },
            'responses': {
                200: {'description': 'Updated full response payload.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Response not found.'},
                409: {'description': 'Draft response cannot be reviewed.'},
                422: {'description': 'Invalid JSON fields or types.'},
            },
        },
    },
)
def questionnaire_response_detail(request, response_id):
    if request.method == 'GET':
        response = response_queryset(include_answers=True).filter(pk=response_id).first()
        if response is None:
            return error_response('Response not found', 'not_found', status=404)
        return JsonResponse(_full_response_payload(response))

    try:
        payload = json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return _validation('body', 'invalid_json')
    if not isinstance(payload, dict) or set(payload) != {'reviewed'}:
        return _validation('body', payload)
    reviewed = payload.get('reviewed')
    if type(reviewed) is not bool:
        return _validation('reviewed', reviewed, ('true', 'false'))
    try:
        response, _changed = transition_response_review(
            response_id=response_id, reviewed=reviewed, actor=request.user,
        )
    except Response.DoesNotExist:
        return error_response('Response not found', 'not_found', status=404)
    except ResponseNotSubmitted:
        return error_response(
            'Only submitted responses can be reviewed',
            'response_not_submitted', status=409,
        )
    response = response_queryset(include_answers=True).get(pk=response.pk)
    return JsonResponse(_full_response_payload(response))
