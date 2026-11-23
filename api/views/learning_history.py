"""Staff-only export of existing course lesson-opening history."""

import hashlib
import json

from django.core import signing
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from analytics.activity import get_user_activity_retention_days
from analytics.services.learning_history import history_page, history_queryset
from api.openapi import openapi_spec
from api.request_parsing import parse_limit, parse_since
from api.safety import error_response
from api.serializers.learning_history import serialize_history
from api.utils import require_methods
from content.models import Course
from content.services.course_cohorts import get_course_cohort_by_key


def _filters(params, course):
    filters = {}
    for name in ('since', 'until'):
        value, error = parse_since(params.get(name), field=name)
        if error is not None:
            return None, error
        if value is not None and timezone.is_naive(value):
            return None, error_response(f'{name} requires a timezone', 'validation_error', status=422)
        filters[name] = value
    for name in ('module', 'unit'):
        if params.get(name):
            try:
                filters[name] = int(params[name])
                if filters[name] <= 0:
                    raise ValueError
            except ValueError:
                return None, error_response(f'Invalid {name} id', 'validation_error', status=422)
    filters['email'] = params.get('email', '').strip()
    if params.get('cohort'):
        cohort = get_course_cohort_by_key(course, params['cohort'])
        if cohort is None:
            return None, error_response('Unknown cohort', 'unknown_cohort', status=404)
        filters['cohort'] = cohort
    return filters, None


def _scope(course, params):
    values = {'course': course.slug}
    for name in ('cohort', 'module', 'unit', 'since', 'until', 'email'):
        values[name] = params.get(name, '')
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _history_response(course, params, filters, limit):
    queryset = history_queryset(course, filters).select_related('user')
    try:
        rows, total, cursor = history_page(
            queryset, limit=limit, cursor=params.get('cursor'), scope=_scope(course, params),
        )
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return error_response('Invalid cursor or changed filters', 'invalid_cursor', status=422)
    return JsonResponse({
        'course': course.slug, 'total_count': total, 'limit': limit,
        'has_more': cursor is not None, 'next_cursor': cursor,
        'activities': serialize_history(rows),
        'measurement': 'lesson_open; not video playback or completion',
        'retention_days': get_user_activity_retention_days(),
        'cohort_scope': 'current enrollment, not cohort at event time',
        'metadata_scope': 'current syllabus; deleted units have null metadata',
        'order': 'insertion id descending; snapshot excludes later inserts',
    })


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('GET', structured_errors=True)
@openapi_spec(
    tag='Learning analytics', summary='Export stored course lesson openings',
    methods={'GET': {
        'description': 'Complete cursor-paginated retained lesson_open history. '
        'Not playback or completion. Cohort uses current enrollment; module '
        'and bonus metadata use current syllabus. since inclusive, until exclusive. '
        'Timezone offsets required. Keep filters unchanged across cursor pages.',
        'query': {
            'limit': {'type': 'integer', 'default': 100, 'maximum': 500},
            'cursor': {'type': 'string'}, 'cohort': {'type': 'string'},
            'module': {'type': 'integer', 'description': 'Module id, includes children'},
            'unit': {'type': 'integer'}, 'email': {'type': 'string'},
            'since': {'type': 'string'}, 'until': {'type': 'string'},
        },
        'responses': {200: {'description': 'Bounded page and next_cursor'},
                      404: {'description': 'Unknown course or cohort'},
                      422: {'description': 'Invalid filter or cursor'}},
    }},
)
def course_learning_history(request, slug):
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response('Unknown course', 'unknown_course', status=404)
    limit, error = parse_limit(request.GET.get('limit'), default=100, maximum=500)
    if error is not None:
        return error
    filters, error = _filters(request.GET, course)
    if error is not None:
        return error
    return _history_response(course, request.GET, filters, limit)
