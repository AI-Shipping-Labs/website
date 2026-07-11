"""Staff-token signup analytics report API."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.lifecycle import ACCOUNT_LIFECYCLE_VALUES
from analytics.services.signup_analytics import (
    API_RECENT_LIMIT_MAX,
    build_signup_analytics_report,
    parse_recent_limit,
    parse_signup_analytics_filters,
    serialize_report,
)
from api.openapi import openapi_spec
from api.utils import require_methods, validation_response

_RESPONSE_EXAMPLE = {
    'filters': {
        'range': '7d',
        'start': '2026-07-02',
        'end': '2026-07-09',
        'start_at': '2026-07-02T12:00:00+00:00',
        'end_at': '2026-07-09T12:00:00+00:00',
        'signup_path': '',
        'signup_path_label': 'All paths',
        'account_lifecycle': '',
        'account_lifecycle_label': 'All lifecycles',
        'limit': 50,
    },
    'headline_cards': [
        {
            'label': 'Last 7d',
            'window_label': '7d',
            'count': 12,
            'delta': {'sign': '+', 'diff': 3},
        },
    ],
    'window_total': 12,
    'actionable_source_rows': [
        {
            'label': 'Spring Launch (spring_launch)',
            'kind': 'campaign',
            'signup_count': 5,
            'percent_share': '41.7%',
            'top_signup_path': 'email_password',
            'top_signup_path_label': 'Email + password',
            'top_pre_signup_landing_path': '/pricing',
            'campaign': {
                'id': 1,
                'name': 'Spring Launch',
                'slug': 'spring_launch',
                'studio_url': '/studio/utm-analytics/campaign/spring_launch/',
            },
        },
    ],
    'account_lifecycle_rows': [
        {
            'account_lifecycle': 'newsletter_only',
            'label': 'Newsletter-only',
            'signup_count': 3,
            'percent_share': '25.0%',
        },
    ],
    'pre_signup_activity_rows': [
        {
            'category': 'Pricing',
            'path': '/pricing',
            'distinct_signup_count': 4,
            'total_tracked_visits': 6,
            'top_actionable_source': 'Spring Launch (spring_launch)',
        },
    ],
    'recent_signups': [
        {
            'user_id': 42,
            'email': 'member@example.com',
            'user_studio_url': '/studio/users/42/',
            'actionable_source': {
                'label': 'Spring Launch (spring_launch)',
                'kind': 'campaign',
                'campaign_slug': 'spring_launch',
            },
            'first_tracked_landing_path': '/pricing',
            'last_tracked_pre_signup_path': '/accounts/register/',
            'tracked_visit_count': 3,
            'top_categories': ['Pricing'],
            'top_categories_label': 'Pricing',
            'account_lifecycle': 'full_account',
            'account_lifecycle_label': 'Full account',
            'signup_path': 'email_password',
            'signup_path_label': 'Email + password',
            'signed_up_at': '2026-07-09T12:00:00+00:00',
        },
    ],
}


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Analytics',
    summary='Read signup analytics',
    methods={
        'GET': {
            'summary': 'Read signup source and pre-signup activity analytics',
            'description': (
                'Returns the same signup analytics report used by Studio: '
                'normalized filters, headline cards, actionable source rows, '
                'pre-signup activity rows, and recent signup journey rows. '
                'Staff-token only, read-only. The response never includes raw '
                'IPs, raw user agents, full query strings, or anonymous IDs.'
            ),
            'query': {
                'range': {
                    'type': 'string',
                    'enum': ['24h', '7d', '30d', 'custom'],
                    'required': False,
                    'description': 'Date range for aggregate sections.',
                },
                'start': {
                    'type': 'string',
                    'format': 'date',
                    'required': False,
                    'description': 'Custom range start date, YYYY-MM-DD.',
                },
                'end': {
                    'type': 'string',
                    'format': 'date',
                    'required': False,
                    'description': 'Custom range end date, YYYY-MM-DD.',
                },
                'signup_path': {
                    'type': 'string',
                    'required': False,
                    'description': 'Optional signup path filter.',
                },
                'account_lifecycle': {
                    'type': 'string',
                    'enum': list(ACCOUNT_LIFECYCLE_VALUES),
                    'required': False,
                    'description': 'Optional derived account lifecycle filter.',
                },
                'limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': API_RECENT_LIMIT_MAX,
                    'required': False,
                    'description': (
                        'Recent-signup row limit. Values above the safe cap '
                        f'are capped at {API_RECENT_LIMIT_MAX}.'
                    ),
                },
            },
            'responses': {
                200: {
                    'description': 'Signup analytics report.',
                    'example': _RESPONSE_EXAMPLE,
                },
                401: {'description': 'Missing or invalid staff token.'},
                422: {
                    'description': 'Invalid filter value.',
                    'schema': {'$ref': '#/components/schemas/ErrorResponse'},
                },
            },
        },
    },
)
def signup_analytics_report(request):
    """Return signup analytics as staff-token JSON."""
    filters, errors = parse_signup_analytics_filters(
        request.GET,
        strict=True,
    )
    limit, limit_errors = parse_recent_limit(request.GET.get('limit'))
    errors.update(limit_errors)
    if errors:
        return validation_response(errors)

    report = build_signup_analytics_report(filters)
    return JsonResponse(serialize_report(report, recent_limit=limit), status=200)


__all__ = ['signup_analytics_report']
