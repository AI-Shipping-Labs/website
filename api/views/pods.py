"""Staff API for pods (issue #1918).

Pairing logic stays in the operator's own tooling: this API gives it the
cohort roster (CRM fields, availability, current pods) and lets it create
pods, add or remove members, and approve or decline requests as an
override. Every write goes through ``pods.services.membership`` so the
rules match the member pages and Studio. Staff-only, so emails are fine.

- ``GET  /api/courses/<slug>/cohorts/<key>/members`` -- pairing roster
- ``GET  /api/pods`` / ``POST /api/pods``
- ``GET  /api/pods/<id>`` / ``PATCH /api/pods/<id>``
- ``POST /api/pods/<id>/members`` / ``DELETE /api/pods/<id>/members/<email>``
- ``GET  /api/pods/<id>/requests``
- ``POST /api/pods/<id>/requests/<request_id>/approve|decline``
- ``GET  /api/pods/<id>/meetings`` / ``POST /api/pods/<id>/meetings``
- ``PATCH /api/pods/<id>/meetings/<meeting_id>`` (issue #1919)

There is no DELETE for a pod: archive with ``PATCH {"status": "archived"}``
so request history is kept. There is no DELETE for a meeting either:
``PATCH {"status": "cancelled"}`` keeps its history.
"""

from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.email_resolution import normalize_email, resolve_users_by_emails
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.datetime import isoformat_or_none
from api.utils import body_must_be_object_response, parse_json_body, require_methods, validation_response
from content.models import CohortEnrollment, Course
from content.services.course_cohorts import get_course_cohort_by_key
from crm.models import CRMRecord
from payments.models import Membership, TierOverride
from pods.models import (
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_CHOICES,
    OPEN_REQUEST_STATUSES,
    POD_SOURCE_API,
    POD_STATUS_CHOICES,
    REQUEST_STATUS_CHOICES,
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_WAITLISTED,
    AvailabilityProfile,
    Pod,
    PodJoinRequest,
    PodMeeting,
    PodMembership,
)
from pods.services import config as pods_config
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.availability import format_minute
from pods.services.meeting_rules import meeting_end, meeting_state
from pods.services.slack import clean_channel_url
from pods.services.stuck_proposals import proposal_waits
from pods.services.suggestions import load_member_availability, suggest_slots

TAG = 'Pods'
DEFAULT_LIMIT = 200
MAX_LIMIT = 500
POD_LIST_DEFAULT_LIMIT = 50
POD_LIST_MAX_LIMIT = 200

_POD_WRITABLE = (
    'name', 'purpose', 'max_members', 'meeting_count', 'meeting_minutes',
    'status', 'owner_email', 'slack_channel_url', 'meeting_url',
)
_MEETING_STATUS_VALUES = [value for value, _label in MEETING_STATUS_CHOICES]
_MEETING_CREATE_FIELDS = ('starts_at', 'timezone', 'repeat_weekly', 'count')
_MEETING_PATCH_FIELDS = ('starts_at', 'timezone', 'move_later', 'status')
_MEETING_PATCH_STATUSES = ['scheduled', 'held', 'cancelled']
_CONFLICT_CODES = ('pod_full', 'request_not_open', mtg.CODE_LIMIT, mtg.CODE_NOT_MOVABLE, mtg.CODE_PROPOSAL_EXISTS)
_STATUS_VALUES = [value for value, _label in POD_STATUS_CHOICES]
_REQUEST_STATUS_VALUES = [value for value, _label in REQUEST_STATUS_CHOICES]

_RESULTS_EXAMPLE = {
    'added': ['anna@example.com', 'raj@example.com'],
    'not_enrolled': ['someone@example.com'],
    'unknown_user': [],
    'already_member': [],
    'over_capacity': [],
}

_POD_SUMMARY_EXAMPLE = {
    'id': 7,
    'course_slug': 'ai-buildcamp',
    'cohort_key': '4',
    'cohort_name': 'Cohort 4',
    'name': 'Agents pod',
    'purpose': 'Ship an agent demo',
    'status': 'open',
    'max_members': 4,
    'member_count': 2,
    'meeting_count': 3,
    'meeting_minutes': 60,
    'pending_count': 1,
    'waitlisted_count': 0,
    'owner_email': 'anna@example.com',
    'source': 'api',
    'slack_channel_url': '',
    'created_at': '2026-10-01T09:00:00+00:00',
}

_MEETING_EXAMPLE = {
    'id': 31,
    'number': 2,
    'starts_at': '2026-10-13T16:00:00+00:00',
    'ends_at': '2026-10-13T17:00:00+00:00',
    'duration_minutes': 60,
    'timezone': 'Europe/Berlin',
    'status': 'scheduled',
    'expired': False,
    'series_id': '6f1c2d4e-8a0b-4c3d-9e5f-1a2b3c4d5e6f',
    'created_via': 'member',
    'proposed_by': 'anna@example.com',
    'moved_by': None,
    'moved_at': None,
    'previous_starts_at': None,
    'reminder_sent_at': None,
    'responses': [{'email': 'mike@example.com', 'response': 'cant_make_it'}],
    'waiting_on': [],
    'stuck': False,
    'stuck_alerted_at': None,
}

_POD_DETAIL_EXAMPLE = {
    **_POD_SUMMARY_EXAMPLE,
    'members': [{
        'email': 'anna@example.com',
        'first_name': 'Anna',
        'last_name': 'Klein',
        'timezone': 'Europe/Berlin',
        'joined_at': '2026-10-01T09:00:00+00:00',
        'source': 'api',
    }],
    'open_requests': [{
        'id': 12,
        'email': 'mike@example.com',
        'status': 'pending',
        'waitlist_position': None,
        'message': 'I am building a RAG eval harness',
        'created_at': '2026-10-02T10:00:00+00:00',
        'stale_alerted_at': None,
    }],
    'meeting_url': 'https://meet.google.com/abc-defg-hij',
    'meetings': [_MEETING_EXAMPLE],
    'suggested_slots': [{
        'start': '2026-10-06T16:00:00+00:00',
        'end': '2026-10-06T17:00:00+00:00',
        'available': ['anna@example.com', 'raj@example.com'],
        'if_needed': [],
        'missing': [],
        'local_starts': [
            {'email': 'anna@example.com', 'timezone': 'Europe/Berlin', 'local_start': '2026-10-06T18:00:00+02:00'},
        ],
    }],
}

_REQUEST_EXAMPLE = {
    'id': 12,
    'email': 'mike@example.com',
    'status': 'pending',
    'waitlist_position': None,
    'message': 'I am building a RAG eval harness',
    'created_at': '2026-10-02T10:00:00+00:00',
    'decided_at': None,
    'decided_by': None,
    'stale_alerted_at': None,
}

_ROSTER_EXAMPLE = {
    'email': 'anna@example.com',
    'first_name': 'Anna',
    'last_name': 'Klein',
    'tier': 'main',
    'tags': ['maven', 'ai-buildcamp', 'ai-buildcamp-4'],
    'preferred_timezone': 'Europe/Berlin',
    'slack_member': True,
    'has_slack_user_id': True,
    'enrolled_at': '2026-09-10T08:00:00+00:00',
    'crm': {
        'status': 'active',
        'persona': 'Builder',
        'summary': 'Ships RAG apps at work.',
        'next_steps': 'Pair with someone in Asia.',
    },
    'availability': {
        'timezone': 'Europe/Berlin',
        'updated_at': '2026-09-20T12:00:00+00:00',
        'windows': [{'weekday': 1, 'start': '18:00', 'end': '21:00', 'preference': 'preferred'}],
    },
    'pod_ids': [7],
    'open_request_pod_ids': [9],
}

_ERR_UNKNOWN_POD = {'error': 'Pod not found', 'code': 'unknown_pod'}
_ERR_LIMIT = {'error': mtg.msg_limit(4), 'code': mtg.CODE_LIMIT}


def _parse_limit(request, default, maximum):
    """Return ``(limit, offset, error_response)``."""
    try:
        limit = int(request.GET.get('limit', default))
        offset = int(request.GET.get('offset', 0))
    except (TypeError, ValueError):
        return None, None, validation_response(
            {'field': 'limit', 'expected': 'integer'}, message='limit and offset must be integers',
        )
    if limit < 1 or limit > maximum or offset < 0:
        return None, None, validation_response(
            {'field': 'limit', 'maximum': maximum},
            message=f'limit must be between 1 and {maximum}; offset must be 0 or more',
        )
    return limit, offset, None


def _pod_queryset():
    return Pod.objects.filter(cohort__isnull=False).select_related('cohort__course', 'owner').annotate(
        member_total=Count('memberships', distinct=True),
        pending_total=Count('join_requests', filter=Q(join_requests__status=REQUEST_STATUS_PENDING), distinct=True),
        waiting_total=Count('join_requests', filter=Q(join_requests__status=REQUEST_STATUS_WAITLISTED), distinct=True),
    )


def _get_pod(pod_id):
    return _pod_queryset().filter(pk=pod_id).first()


def _unknown_pod():
    return error_response('Pod not found', 'unknown_pod', status=404)


def _serialize_summary(pod):
    """``pod`` must come from ``_pod_queryset`` (count annotations)."""
    return {
        'id': pod.pk,
        'course_slug': pod.cohort.course.slug,
        'cohort_key': pod.cohort.external_key,
        'cohort_name': pod.cohort.name,
        'name': pod.name,
        'purpose': pod.purpose,
        'status': pod.status,
        'max_members': pod.max_members,
        'member_count': pod.member_total,
        'meeting_count': pod.meeting_count,
        'meeting_minutes': pod.meeting_minutes,
        'pending_count': pod.pending_total,
        'waitlisted_count': pod.waiting_total,
        'owner_email': pod.owner.email if pod.owner_id else None,
        'source': pod.source,
        'slack_channel_url': pod.slack_channel_url,
        'created_at': isoformat_or_none(pod.created_at),
    }


def _serialize_request(join_request, position=None):
    return {
        'id': join_request.pk,
        'email': join_request.user.email,
        'status': join_request.status,
        'waitlist_position': position,
        'message': join_request.message,
        'created_at': isoformat_or_none(join_request.created_at),
        'decided_at': isoformat_or_none(join_request.decided_at),
        'decided_by': join_request.decided_by.email if join_request.decided_by_id else None,
        'stale_alerted_at': isoformat_or_none(join_request.stale_alerted_at),
    }


def _waitlist_positions(pod):
    ids = pod.join_requests.filter(status=REQUEST_STATUS_WAITLISTED).order_by('created_at', 'pk').values_list('pk', flat=True)
    return {request_id: index + 1 for index, request_id in enumerate(ids)}


def _serialize_detail(pod):
    pod = _get_pod(pod.pk)
    memberships = list(pod.memberships.select_related('user').order_by('joined_at', 'pk'))
    users = [m.user for m in memberships]
    availability = load_member_availability(users)
    positions = _waitlist_positions(pod)
    open_requests = pod.join_requests.filter(
        status__in=OPEN_REQUEST_STATUSES,
    ).select_related('user', 'decided_by').order_by('created_at', 'pk')
    member_avail = [availability[u.pk] for u in users]
    slots = suggest_slots(member_avail, pod.meeting_minutes, all_members=member_avail)
    data = _serialize_summary(pod)
    data.update({
        'meeting_url': pod.meeting_url,
        'meetings': _serialize_meetings(pod, exclude_cancelled=True),
        'members': [{
            'email': m.user.email,
            'first_name': m.user.first_name,
            'last_name': m.user.last_name,
            'timezone': availability[m.user_id].timezone_name or None,
            'joined_at': isoformat_or_none(m.joined_at),
            'source': m.source,
        } for m in memberships],
        'open_requests': [
            {key: value for key, value in _serialize_request(r, positions.get(r.pk)).items()
             if key not in ('decided_at', 'decided_by')}
            for r in open_requests
        ],
        'suggested_slots': [{
            'start': isoformat_or_none(slot.start),
            'end': isoformat_or_none(slot.end),
            'available': [m.user.email for m in slot.available],
            'if_needed': [m.user.email for m in slot.if_needed],
            'missing': [m.user.email for m in slot.missing],
            'local_starts': [{
                'email': local['member'].user.email,
                'timezone': local['timezone'],
                'local_start': isoformat_or_none(slot.start.astimezone(ZoneInfo(local['timezone']))),
            } for local in slot.local_starts],
        } for slot in slots],
    })
    return data


def _meeting_queryset(pod):
    return (
        PodMeeting.objects.filter(pod=pod)
        .select_related('proposed_by', 'moved_by')
        .prefetch_related('responses__user')
        .order_by('starts_at', 'pk')
    )


def _serialize_meeting(meeting, state, waits):
    # Issue #1935: ``waiting_on``/``stuck`` only for live proposal heads.
    wait = waits.get(meeting.pk)
    return {
        'id': meeting.pk,
        'number': state.numbers.get(meeting.pk),
        'starts_at': isoformat_or_none(meeting.starts_at),
        'ends_at': isoformat_or_none(meeting_end(meeting)),
        'duration_minutes': meeting.duration_minutes,
        'timezone': meeting.timezone,
        'status': meeting.status,
        'expired': state.is_expired(meeting),
        'series_id': str(meeting.series_id) if meeting.series_id else None,
        'created_via': meeting.created_via,
        'proposed_by': meeting.proposed_by.email if meeting.proposed_by_id else None,
        'moved_by': meeting.moved_by.email if meeting.moved_by_id else None,
        'moved_at': isoformat_or_none(meeting.moved_at),
        'previous_starts_at': isoformat_or_none(meeting.previous_starts_at),
        'reminder_sent_at': isoformat_or_none(meeting.reminder_sent_at),
        'responses': [
            {'email': response.user.email, 'response': response.response}
            for response in sorted(meeting.responses.all(), key=lambda r: r.pk)
        ],
        'waiting_on': [user.email for user in wait.waiting] if wait else [],
        'stuck': bool(wait and wait.stuck),
        'stuck_alerted_at': isoformat_or_none(meeting.stuck_alerted_at),
    }


def _serialize_meetings(pod, *, exclude_cancelled=False, status=None, only_ids=None):
    meetings = list(_meeting_queryset(pod))
    state = meeting_state(pod, meetings=meetings)
    members = [m.user for m in pod.memberships.select_related('user').order_by('joined_at', 'pk')]
    waits = proposal_waits(
        pod, state, members, [r for m in meetings for r in m.responses.all()],
        hours=pods_config.stuck_proposal_hours(),
    )
    rows = []
    for meeting in state.meetings:
        if exclude_cancelled and meeting.status == MEETING_STATUS_CANCELLED:
            continue
        if status and meeting.status != status:
            continue
        if only_ids is not None and meeting.pk not in only_ids:
            continue
        rows.append(_serialize_meeting(meeting, state, waits))
    return rows


def _pod_error_response(exc):
    status = 409 if exc.code in _CONFLICT_CODES else 422
    if exc.code == 'not_a_member':
        status = 404
    details = {'field': exc.field} if exc.field else None
    return error_response(exc.message, exc.code, status=status, details=details)


# --- Roster -----------------------------------------------------------------

def _effective_tiers(user_ids):
    """``{user_id: tier_slug}``: max of paid tier and active override."""
    tiers = {}
    for membership in Membership.objects.filter(user_id__in=user_ids).select_related('tier'):
        if membership.tier_id is not None:
            tiers[membership.user_id] = (membership.tier.level, membership.tier.slug)
    for override in TierOverride.objects.filter(
        user_id__in=user_ids, is_active=True, expires_at__gt=timezone.now(),
    ).select_related('override_tier'):
        current = tiers.get(override.user_id, (0, 'free'))
        if override.override_tier.level > current[0]:
            tiers[override.user_id] = (override.override_tier.level, override.override_tier.slug)
    return {user_id: slug for user_id, (_level, slug) in tiers.items()}


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('GET', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Cohort roster for pairing (staff-only)',
    methods={
        'GET': {
            'summary': 'List a cohort roster with CRM, availability and pods (staff-only)',
            'description': (
                'Every user with a ``CohortEnrollment`` in the cohort (and only '
                'them), ordered by enrollment time. ``<key>`` is the cohort '
                'external key (the ``?cohort=`` value), case-insensitive. '
                '``tier`` is the effective tier slug (paid tier or active '
                'override, whichever is higher). ``crm`` is ``null`` without a '
                'CRM record; ``availability`` is ``null`` without a profile and '
                'its windows are local wall-clock times in its ``timezone``. '
                '``pod_ids`` are the cohort pods the user is in; '
                '``open_request_pod_ids`` the cohort pods with a pending or '
                'waitlisted request.'
            ),
            'query': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_LIMIT, 'default': DEFAULT_LIMIT},
                'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
            },
            'responses': {
                200: {
                    'description': 'Roster page.',
                    'example': {
                        'course': 'ai-buildcamp', 'cohort_key': '4', 'total': 1,
                        'limit': DEFAULT_LIMIT, 'offset': 0, 'members': [_ROSTER_EXAMPLE],
                    },
                },
                401: {'description': 'Missing or invalid staff token.',
                      'example': {'error': 'Authentication token required', 'code': 'authentication_required'}},
                404: {'description': 'Unknown course or cohort.',
                      'example': {'error': "Unknown cohort '9' for course 'ai-buildcamp'", 'code': 'unknown_cohort'}},
                422: {'description': 'Bad limit or offset.',
                      'example': {'error': 'limit must be between 1 and 500; offset must be 0 or more', 'code': 'validation_error'}},
            },
        },
    },
)
def cohort_roster(request, slug, key):
    course = Course.objects.filter(slug=slug).first()
    if course is None:
        return error_response('Course not found', 'unknown_course', status=404)
    cohort = get_course_cohort_by_key(course, key)
    if cohort is None:
        return error_response(f'Unknown cohort {key!r} for course {course.slug!r}', 'unknown_cohort', status=404)
    limit, offset, error = _parse_limit(request, DEFAULT_LIMIT, MAX_LIMIT)
    if error is not None:
        return error
    enrollments = CohortEnrollment.objects.filter(cohort=cohort).select_related('user').order_by('enrolled_at', 'pk')
    total = enrollments.count()
    page = list(enrollments[offset:offset + limit])
    user_ids = [e.user_id for e in page]
    tiers = _effective_tiers(user_ids)
    crm = {r.user_id: r for r in CRMRecord.objects.filter(user_id__in=user_ids)}
    profiles = {
        p.user_id: p for p in AvailabilityProfile.objects.filter(user_id__in=user_ids).prefetch_related('windows')
    }
    pod_ids, request_pod_ids = {}, {}
    for user_id, pod_id in PodMembership.objects.filter(
        user_id__in=user_ids, pod__cohort=cohort,
    ).values_list('user_id', 'pod_id').order_by('pod_id'):
        pod_ids.setdefault(user_id, []).append(pod_id)
    for user_id, pod_id in PodJoinRequest.objects.filter(
        user_id__in=user_ids, pod__cohort=cohort, status__in=OPEN_REQUEST_STATUSES,
    ).values_list('user_id', 'pod_id').order_by('pod_id'):
        request_pod_ids.setdefault(user_id, []).append(pod_id)

    members = []
    for enrollment in page:
        user = enrollment.user
        record = crm.get(user.pk)
        profile = profiles.get(user.pk)
        members.append({
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'tier': tiers.get(user.pk, 'free'),
            'tags': list(user.tags or []),
            'preferred_timezone': user.preferred_timezone or '',
            'slack_member': bool(user.slack_member),
            'has_slack_user_id': bool(user.slack_user_id),
            'enrolled_at': isoformat_or_none(enrollment.enrolled_at),
            'crm': None if record is None else {
                'status': record.status,
                'persona': record.persona,
                'summary': record.summary,
                'next_steps': record.next_steps,
            },
            'availability': None if profile is None else {
                'timezone': profile.timezone,
                'updated_at': isoformat_or_none(profile.updated_at),
                'windows': [{
                    'weekday': w.weekday,
                    'start': format_minute(w.start_minute),
                    'end': format_minute(w.end_minute),
                    'preference': w.preference,
                } for w in profile.windows.all()],
            },
            'pod_ids': pod_ids.get(user.pk, []),
            'open_request_pod_ids': request_pod_ids.get(user.pk, []),
        })
    return JsonResponse({
        'course': course.slug,
        'cohort_key': cohort.external_key,
        'total': total,
        'limit': limit,
        'offset': offset,
        'members': members,
    })


# --- Pods collection --------------------------------------------------------

def _create_pod(request):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    for field in ('course_slug', 'cohort_key', 'name', 'purpose'):
        if not str(data.get(field) or '').strip():
            return validation_response({'field': field}, message=f'{field} is required')
    member_emails = data.get('member_emails') or []
    if not isinstance(member_emails, list):
        return validation_response({'field': 'member_emails', 'expected': 'array of strings'},
                                   message='member_emails must be a list')
    course = Course.objects.filter(slug=str(data['course_slug']).strip()).first()
    if course is None:
        return error_response('Course not found', 'unknown_course', status=404)
    cohort = get_course_cohort_by_key(course, data['cohort_key'])
    if cohort is None:
        return error_response(
            f"Unknown cohort {data['cohort_key']!r} for course {course.slug!r}", 'unknown_cohort', status=404,
        )
    fields = {key: data.get(key) for key in ('name', 'purpose', 'max_members', 'meeting_count', 'meeting_minutes')}
    try:
        pod, results, created = svc.create_staff_pod(
            cohort=cohort,
            data=fields,
            owner_email=data.get('owner_email') or '',
            member_emails=member_emails,
            actor=request.user,
            source=POD_SOURCE_API,
        )
    except svc.PodError as exc:
        return _pod_error_response(exc)
    payload = _serialize_detail(pod)
    payload['results'] = results.as_dict()
    return JsonResponse(payload, status=201 if created else 200)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('GET', 'POST', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='List or create pods (staff-only)',
    methods={
        'GET': {
            'summary': 'List pods (staff-only)',
            'description': 'Pod summaries, newest first. Filter by ``course`` slug, ``cohort`` key (needs ``course``) and ``status``.',
            'query': {
                'course': {'type': 'string'},
                'cohort': {'type': 'string'},
                'status': {'type': 'string', 'enum': _STATUS_VALUES},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': POD_LIST_MAX_LIMIT, 'default': POD_LIST_DEFAULT_LIMIT},
                'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
            },
            'responses': {
                200: {'description': 'Pod page.',
                      'example': {'total': 1, 'limit': POD_LIST_DEFAULT_LIMIT, 'offset': 0, 'pods': [_POD_SUMMARY_EXAMPLE]}},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown course or cohort filter.',
                      'example': {'error': 'Course not found', 'code': 'unknown_course'}},
                422: {'description': 'Bad status, limit or offset.'},
            },
        },
        'POST': {
            'summary': 'Create a pod (staff-only)',
            'description': (
                'Creates a pod in a dated cohort with ``source=api``. The '
                'optional ``owner_email`` must be enrolled in the cohort and '
                'becomes the first member. ``member_emails`` are added up to '
                'the size limit; ``results`` sorts every email into ``added``, '
                '``not_enrolled``, ``unknown_user``, ``already_member`` and '
                '``over_capacity``. A self-paced cohort returns 422. '
                'Idempotent: when a non-archived pod with the same name '
                '(case-insensitive) already exists in the cohort, it is '
                'returned with 200 and only new emails are added; its '
                'settings and owner are left unchanged (use PATCH).'
            ),
            'request_body': {
                'required': ['course_slug', 'cohort_key', 'name', 'purpose'],
                'properties': {
                    'course_slug': {'type': 'string'},
                    'cohort_key': {'type': 'string'},
                    'name': {'type': 'string', 'maxLength': 80},
                    'purpose': {'type': 'string', 'maxLength': 500},
                    'max_members': {'type': 'integer', 'minimum': 1, 'maximum': 12},
                    'meeting_count': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'meeting_minutes': {'type': 'integer', 'enum': [30, 45, 60, 90]},
                    'owner_email': {'type': ['string', 'null']},
                    'member_emails': {'type': 'array', 'items': {'type': 'string'}},
                },
                'example': {
                    'course_slug': 'ai-buildcamp', 'cohort_key': '4', 'name': 'Agents pod',
                    'purpose': 'Ship an agent demo', 'owner_email': 'anna@example.com',
                    'member_emails': ['anna@example.com', 'raj@example.com', 'someone@example.com'],
                },
            },
            'responses': {
                200: {'description': 'A pod with this name already exists in the cohort; new emails were added.',
                      'example': {**_POD_DETAIL_EXAMPLE, 'results': {**_RESULTS_EXAMPLE,
                                  'added': [], 'already_member': ['anna@example.com', 'raj@example.com']}}},
                201: {'description': 'Pod created.', 'example': {**_POD_DETAIL_EXAMPLE, 'results': _RESULTS_EXAMPLE}},
                400: {'description': 'Invalid JSON or non-object body.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown course or cohort.',
                      'example': {'error': "Unknown cohort '9' for course 'ai-buildcamp'", 'code': 'unknown_cohort'}},
                422: {'description': 'Validation error, including a self-paced cohort or an owner who is not enrolled.',
                      'example': {'error': 'Pods need a dated cohort. A self-paced cohort cannot have pods.', 'code': 'validation_error'}},
            },
        },
    },
)
def pods_collection(request):
    if request.method == 'POST':
        return _create_pod(request)
    pods = _pod_queryset().order_by('-created_at', '-pk')
    course_slug = (request.GET.get('course') or '').strip()
    cohort_key = (request.GET.get('cohort') or '').strip()
    status = (request.GET.get('status') or '').strip()
    if course_slug:
        course = Course.objects.filter(slug=course_slug).first()
        if course is None:
            return error_response('Course not found', 'unknown_course', status=404)
        pods = pods.filter(cohort__course=course)
        if cohort_key:
            cohort = get_course_cohort_by_key(course, cohort_key)
            if cohort is None:
                return error_response(
                    f'Unknown cohort {cohort_key!r} for course {course.slug!r}', 'unknown_cohort', status=404,
                )
            pods = pods.filter(cohort=cohort)
    elif cohort_key:
        return validation_response({'field': 'cohort'}, message='cohort needs course')
    if status:
        if status not in _STATUS_VALUES:
            return validation_response({'field': 'status', 'allowed': _STATUS_VALUES}, message='Unknown status')
        pods = pods.filter(status=status)
    limit, offset, error = _parse_limit(request, POD_LIST_DEFAULT_LIMIT, POD_LIST_MAX_LIMIT)
    if error is not None:
        return error
    total = pods.count()
    return JsonResponse({
        'total': total,
        'limit': limit,
        'offset': offset,
        'pods': [_serialize_summary(pod) for pod in pods[offset:offset + limit]],
    })


# --- Pod detail -------------------------------------------------------------

def _patch_pod(request, pod):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    unknown = sorted(set(data) - set(_POD_WRITABLE))
    if unknown:
        return validation_response({'field': unknown[0], 'allowed': list(_POD_WRITABLE)},
                                   message=f'Unknown field(s): {", ".join(unknown)}')
    if not data:
        return validation_response({'field': 'body', 'allowed': list(_POD_WRITABLE)}, message='Nothing to update')
    changes = {
        key: value for key, value in data.items() if key not in ('owner_email', 'slack_channel_url', 'meeting_url')
    }
    if 'owner_email' in data:
        email = normalize_email(data['owner_email'] or '')
        if not email:
            changes['owner'] = None
        else:
            owner = resolve_users_by_emails([email]).get(email)
            if owner is None or not pod.memberships.filter(user=owner).exists():
                return validation_response({'field': 'owner_email'}, message='The owner must be a member of the pod.')
            changes['owner'] = owner
    if 'slack_channel_url' in data:
        try:
            changes['slack_channel_url'] = clean_channel_url(data['slack_channel_url'] or '')
        except ValueError as exc:
            return validation_response({'field': 'slack_channel_url'}, message=str(exc))
    if 'meeting_url' in data:
        if data['meeting_url'] is not None and not isinstance(data['meeting_url'], str):
            return validation_response({'field': 'meeting_url', 'expected': 'string'}, message=mtg.MSG_CALL_LINK)
        try:
            changes['meeting_url'] = mtg.clean_call_link(data['meeting_url'] or '')
        except ValueError as exc:
            return validation_response({'field': 'meeting_url'}, message=str(exc))
    try:
        svc.update_pod(pod, changes, actor=request.user, staff=True)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return JsonResponse(_serialize_detail(pod))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('GET', 'PATCH', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Read or update a pod (staff-only)',
    methods={
        'GET': {
            'summary': 'Pod detail (staff-only)',
            'description': (
                'Settings, members, open requests (pending and waitlisted with '
                'their 1-based waiting-list position), the Slack channel link, '
                'the call link ``meeting_url``, ``meetings`` (every meeting that '
                'is not cancelled, same shape as ``GET /api/pods/<id>/meetings``) '
                'and ``suggested_slots``: up to ``PODS_SUGGESTION_COUNT`` UTC '
                'slots with ``available``, ``if_needed`` and ``missing`` member '
                'emails and each member\'s local start time.'
            ),
            'responses': {
                200: {'description': 'Pod detail.', 'example': _POD_DETAIL_EXAMPLE},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod.', 'example': _ERR_UNKNOWN_POD},
            },
        },
        'PATCH': {
            'summary': 'Update a pod (staff-only)',
            'description': (
                'Partial update. ``max_members`` below the member count returns '
                '422; raising it moves the oldest waitlisted requests back to '
                '``pending`` (one per new seat). ``owner_email`` must be a member, '
                'or ``null`` for no owner. ``status=archived`` hides the pod from '
                'members and cancels its open requests; there is no DELETE. '
                '``slack_channel_url`` must be an https Slack link (empty clears). '
                '``meeting_url`` is the pod call link: https only, up to 500 '
                'characters, empty clears. ``meeting_count`` below the meetings '
                'already planned or held returns 422.'
            ),
            'request_body': {
                'properties': {
                    'name': {'type': 'string'},
                    'purpose': {'type': 'string'},
                    'max_members': {'type': 'integer', 'minimum': 1, 'maximum': 12},
                    'meeting_count': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'meeting_minutes': {'type': 'integer', 'enum': [30, 45, 60, 90]},
                    'status': {'type': 'string', 'enum': _STATUS_VALUES},
                    'owner_email': {'type': ['string', 'null']},
                    'slack_channel_url': {'type': 'string'},
                    'meeting_url': {'type': ['string', 'null'], 'maxLength': 500},
                },
                'example': {'max_members': 5, 'status': 'closed', 'meeting_url': 'https://meet.google.com/abc-defg-hij'},
            },
            'responses': {
                200: {'description': 'Updated pod.', 'example': _POD_DETAIL_EXAMPLE},
                400: {'description': 'Invalid JSON or non-object body.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod.', 'example': _ERR_UNKNOWN_POD},
                422: {'description': 'Validation error.',
                      'example': {'error': 'The size limit cannot be lower than the current number of members (3).',
                                  'code': 'validation_error'}},
            },
        },
    },
)
def pod_detail(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    if request.method == 'PATCH':
        return _patch_pod(request, pod)
    return JsonResponse(_serialize_detail(pod))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('POST', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Add pod members (staff-only)',
    methods={
        'POST': {
            'summary': 'Add members by email (staff-only)',
            'description': (
                'Idempotent. Adds enrolled cohort members up to the size limit '
                'and closes their open requests on this pod as ``approved``. '
                'Returns the same ``results`` buckets as pod create.'
            ),
            'request_body': {
                'required': ['emails'],
                'properties': {'emails': {'type': 'array', 'items': {'type': 'string'}}},
                'example': {'emails': ['raj@example.com']},
            },
            'responses': {
                200: {'description': 'Per-email results and the pod.',
                      'example': {'results': _RESULTS_EXAMPLE, 'pod': _POD_DETAIL_EXAMPLE}},
                400: {'description': 'Invalid JSON or non-object body.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod.', 'example': _ERR_UNKNOWN_POD},
                422: {'description': 'emails missing or not a list.'},
            },
        },
    },
)
def pod_members(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    emails = data.get('emails')
    if not isinstance(emails, list):
        return validation_response({'field': 'emails', 'expected': 'array of strings'}, message='emails must be a list')
    results = svc.add_members_by_email(pod, emails, actor=request.user, source=POD_SOURCE_API)
    return JsonResponse({'results': results.as_dict(), 'pod': _serialize_detail(pod)})


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('DELETE', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Remove a pod member (staff-only)',
    methods={
        'DELETE': {
            'summary': 'Remove a member (staff-only)',
            'description': (
                'Removes the member with the normal leave rules: an owner hands '
                'ownership to the earliest-joined remaining member, a freed seat '
                'moves the oldest waitlisted request back to pending, and an '
                'emptied member-started pod is archived.'
            ),
            'responses': {
                204: {'description': 'Removed.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod or not a member.',
                      'example': {'error': svc.MSG_NOT_A_MEMBER, 'code': 'not_a_member'}},
            },
        },
    },
)
def pod_member_detail(request, pod_id, email):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    normalized = normalize_email(email)
    user = resolve_users_by_emails([normalized]).get(normalized) if normalized else None
    if user is None or not pod.memberships.filter(user=user).exists():
        return error_response(svc.MSG_NOT_A_MEMBER, 'not_a_member', status=404)
    try:
        svc.remove_member(pod, user, actor=request.user)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return HttpResponse(status=204)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('GET', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='List pod requests (staff-only)',
    methods={
        'GET': {
            'summary': 'List join requests (staff-only)',
            'description': 'Every request for the pod, oldest first; filter with ``status``.',
            'query': {'status': {'type': 'string', 'enum': _REQUEST_STATUS_VALUES}},
            'responses': {
                200: {'description': 'Requests.', 'example': {'requests': [_REQUEST_EXAMPLE]}},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod.', 'example': _ERR_UNKNOWN_POD},
                422: {'description': 'Unknown status filter.'},
            },
        },
    },
)
def pod_requests(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    requests = pod.join_requests.select_related('user', 'decided_by').order_by('created_at', 'pk')
    status = (request.GET.get('status') or '').strip()
    if status:
        if status not in _REQUEST_STATUS_VALUES:
            return validation_response({'field': 'status', 'allowed': _REQUEST_STATUS_VALUES}, message='Unknown status')
        requests = requests.filter(status=status)
    positions = _waitlist_positions(pod)
    return JsonResponse({'requests': [_serialize_request(r, positions.get(r.pk)) for r in requests]})


def _decision_spec(verb, extra_conflict):
    return {
        'POST': {
            'summary': f'{verb} a request (staff override)',
            'description': f'{verb}s a pending or waitlisted request regardless of the pod owner.{extra_conflict}',
            'request_body': {'body_required': False, 'properties': {}},
            'responses': {
                200: {'description': 'The decided request.', 'example': {**_REQUEST_EXAMPLE, 'status': verb.lower() + 'd'}},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod or request.',
                      'example': {'error': 'Request not found', 'code': 'unknown_request'}},
                409: {'description': 'Request already decided' + (' or the pod is full.' if extra_conflict else '.'),
                      'example': {'error': svc.MSG_REQUEST_NOT_OPEN, 'code': 'request_not_open'}},
            },
        },
    }


def _decide(request, pod_id, request_id, decide):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    join_request = pod.join_requests.filter(pk=request_id).first()
    if join_request is None:
        return error_response('Request not found', 'unknown_request', status=404)
    try:
        decided = decide(join_request, request.user)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    decided = PodJoinRequest.objects.select_related('user', 'decided_by').get(pk=decided.pk)
    return JsonResponse(_serialize_request(decided))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('POST', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Approve a request (staff override)',
    methods=_decision_spec('Approve', ' A full pod returns 409 ``pod_full``; raise the size limit first.'),
)
def pod_request_approve(request, pod_id, request_id):
    return _decide(request, pod_id, request_id, svc.approve_request)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('POST', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Decline a request (staff override)',
    methods=_decision_spec('Decline', ''),
)
def pod_request_decline(request, pod_id, request_id):
    return _decide(request, pod_id, request_id, svc.decline_request)


# --- Meetings (issue #1919) -----------------------------------------------------

def _parse_bool(data, field, default=False):
    value = data.get(field, default)
    if not isinstance(value, bool):
        return None, validation_response({'field': field, 'expected': 'boolean'}, message=f'{field} must be true or false')
    return value, None


def _create_meetings(request, pod):
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    unknown = sorted(set(data) - set(_MEETING_CREATE_FIELDS))
    if unknown:
        return validation_response({'field': unknown[0], 'allowed': list(_MEETING_CREATE_FIELDS)},
                                   message=f'Unknown field(s): {", ".join(unknown)}')
    for field in ('starts_at', 'timezone'):
        if not isinstance(data.get(field), str) or not data[field].strip():
            return validation_response({'field': field}, message=f'{field} is required')
    repeat_weekly, error = _parse_bool(data, 'repeat_weekly')
    if error is not None:
        return error
    count = data.get('count')
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        return validation_response({'field': 'count', 'expected': 'integer >= 1'}, message='count must be 1 or more')
    try:
        start = mtg.parse_instant(data['starts_at'], field='starts_at')
        created = mtg.schedule_meetings(
            pod, request.user, start, zone_name=data['timezone'], repeat_weekly=repeat_weekly,
            count=count, created_via=POD_SOURCE_API,
        )
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return JsonResponse({'meetings': _serialize_meetings(pod, only_ids={m.pk for m in created})}, status=201)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('GET', 'POST', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='List or schedule pod meetings (staff-only)',
    methods={
        'GET': {
            'summary': 'List pod meetings (staff-only)',
            'description': (
                'Every meeting of the pod in start order, including cancelled '
                'ones; filter with ``status``. ``number`` is the 1-based position '
                'among meetings that are neither cancelled nor expired (``null`` '
                'otherwise). A proposal is ``expired`` once its start passed '
                'while it was still ``proposed``. ``responses`` hold the explicit '
                'answers; after agreement a member without an answer counts as '
                'going. On a live proposal head (the first meeting of a weekly '
                'proposal) ``waiting_on`` lists the member emails with no answer '
                'yet and ``stuck`` is true once it waited more than '
                '``PODS_STUCK_PROPOSAL_HOURS`` since it was proposed or last '
                'moved on a non-archived pod; every other meeting has '
                '``waiting_on: []`` and ``stuck: false``. ``stuck_alerted_at`` is '
                'when the daily staff Slack alert announced it (cleared on move).'
            ),
            'query': {'status': {'type': 'string', 'enum': _MEETING_STATUS_VALUES}},
            'responses': {
                200: {'description': 'Meetings.', 'example': {'meetings': [_MEETING_EXAMPLE]}},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod.', 'example': _ERR_UNKNOWN_POD},
                422: {'description': 'Unknown status filter.'},
            },
        },
        'POST': {
            'summary': 'Schedule pod meetings (staff-only)',
            'description': (
                'Creates confirmed (``scheduled``) meetings with '
                '``created_via=api`` and notifies the members. ``starts_at`` is '
                'an ISO 8601 instant with an offset, on the quarter hour, at '
                'least 1 hour ahead and at most 180 days ahead. ``timezone`` is '
                'the IANA zone weekly repeats keep their local time in. '
                '``count`` defaults to 1, or to every remaining meeting with '
                '``repeat_weekly``; more than 1 needs ``repeat_weekly``. A repeat '
                'that overlaps another meeting moves on a week. Over '
                '``meeting_count`` returns 409 ``meeting_limit_reached``.'
            ),
            'request_body': {
                'required': ['starts_at', 'timezone'],
                'properties': {
                    'starts_at': {'type': 'string', 'format': 'date-time'},
                    'timezone': {'type': 'string'},
                    'repeat_weekly': {'type': 'boolean', 'default': False},
                    'count': {'type': 'integer', 'minimum': 1},
                },
                'example': {'starts_at': '2026-10-13T18:00:00+02:00', 'timezone': 'Europe/Berlin', 'repeat_weekly': True},
            },
            'responses': {
                201: {'description': 'Created meetings.', 'example': {'meetings': [_MEETING_EXAMPLE]}},
                400: {'description': 'Invalid JSON or non-object body.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod.', 'example': _ERR_UNKNOWN_POD},
                409: {'description': 'The pod already has all its meetings planned.', 'example': _ERR_LIMIT},
                422: {'description': 'Bad time (past, over 180 days, off the quarter hour), overlap, or bad field.',
                      'example': {'error': mtg.MSG_TIME_RANGE, 'code': 'validation_error',
                                  'details': {'field': 'start'}}},
            },
        },
    },
)
def pod_meetings(request, pod_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    if request.method == 'POST':
        return _create_meetings(request, pod)
    status = (request.GET.get('status') or '').strip()
    if status and status not in _MEETING_STATUS_VALUES:
        return validation_response({'field': 'status', 'allowed': _MEETING_STATUS_VALUES}, message='Unknown status')
    return JsonResponse({'meetings': _serialize_meetings(pod, status=status or None)})


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('PATCH', structured_errors=True)
@openapi_spec(
    tag=TAG,
    summary='Move a pod meeting or set its status (staff-only)',
    methods={
        'PATCH': {
            'summary': 'Move a meeting or set its status (staff-only)',
            'description': (
                'Send ``starts_at`` to move (with optional ``timezone``, default '
                "the meeting's own zone, and ``move_later`` to move the later "
                'meetings of its weekly series to the same weekly time; all or '
                'nothing on overlap), or ``status`` (``scheduled``, ``held`` or '
                '``cancelled``) to set it as a staff override. Moving keeps the '
                "status, clears ``Can't make it`` answers and the reminder. "
                'Moving a past, held or cancelled meeting returns 409 '
                '``meeting_not_movable``; ``held`` on a proposal returns 422 '
                '(schedule it first); ``cancelled`` on a proposal cancels the '
                'whole proposal; bringing a meeting back to '
                '``scheduled`` over ``meeting_count`` returns 409 '
                '``meeting_limit_reached``. Returns the changed meetings. There '
                'is no DELETE: cancel instead so history is kept.'
            ),
            'request_body': {
                'properties': {
                    'starts_at': {'type': 'string', 'format': 'date-time'},
                    'timezone': {'type': 'string'},
                    'move_later': {'type': 'boolean', 'default': False},
                    'status': {'type': 'string', 'enum': _MEETING_PATCH_STATUSES},
                },
                'example': {'starts_at': '2026-10-15T17:00:00+02:00', 'timezone': 'Europe/Berlin', 'move_later': True},
            },
            'responses': {
                200: {'description': 'Changed meetings.', 'example': {'meetings': [_MEETING_EXAMPLE]}},
                400: {'description': 'Invalid JSON or non-object body.'},
                401: {'description': 'Missing or invalid staff token.'},
                404: {'description': 'Unknown pod or a meeting of another pod.',
                      'example': {'error': 'Meeting not found', 'code': 'unknown_meeting'}},
                409: {'description': 'Meeting cannot be moved, or the meeting limit is reached.',
                      'example': {'error': mtg.MSG_HAPPENED, 'code': mtg.CODE_NOT_MOVABLE}},
                422: {'description': 'Validation error, including status held before the meeting starts.',
                      'example': {'error': mtg.MSG_QUARTER, 'code': 'validation_error', 'details': {'field': 'start'}}},
            },
        },
    },
)
def pod_meeting_detail(request, pod_id, meeting_id):
    pod = _get_pod(pod_id)
    if pod is None:
        return _unknown_pod()
    meeting = PodMeeting.objects.filter(pk=meeting_id, pod=pod).first()
    if meeting is None:
        return error_response('Meeting not found', 'unknown_meeting', status=404)
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()
    unknown = sorted(set(data) - set(_MEETING_PATCH_FIELDS))
    if unknown:
        return validation_response({'field': unknown[0], 'allowed': list(_MEETING_PATCH_FIELDS)},
                                   message=f'Unknown field(s): {", ".join(unknown)}')
    if ('starts_at' in data) == ('status' in data):
        return validation_response({'field': 'body', 'allowed': list(_MEETING_PATCH_FIELDS)},
                                   message='Send either starts_at or status')
    try:
        if 'status' in data:
            if data['status'] not in _MEETING_PATCH_STATUSES:
                return validation_response({'field': 'status', 'allowed': _MEETING_PATCH_STATUSES},
                                           message='Unknown status')
            changed = mtg.set_meeting_status(meeting, request.user, data['status'])
        else:
            move_later, error = _parse_bool(data, 'move_later')
            if error is not None:
                return error
            zone = data.get('timezone') or meeting.timezone
            if not isinstance(zone, str):
                return validation_response({'field': 'timezone', 'expected': 'string'}, message=mtg.MSG_BAD_ZONE)
            start = mtg.parse_instant(data['starts_at'], field='starts_at')
            changed = mtg.move_meeting(meeting, request.user, start, zone_name=zone, move_later=move_later)
    except svc.PodError as exc:
        return _pod_error_response(exc)
    return JsonResponse({'meetings': _serialize_meetings(pod, only_ids={m.pk for m in changed})})
