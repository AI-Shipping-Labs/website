"""View models for the member Pods tab and pod page (issue #1918).

Everything a template needs is computed here so the templates stay free of
business rules. Member surfaces never expose email addresses: people are
named through ``people.display_name``.
"""

from dataclasses import dataclass
from types import SimpleNamespace

from django.urls import reverse
from django.utils import timezone

from accounts.services.timezones import format_user_datetime
from pods.models import (
    OPEN_REQUEST_STATUSES,
    POD_STATUS_ARCHIVED,
    POD_STATUS_OPEN,
    REQUEST_STATUS_DECLINED,
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_WAITLISTED,
    Pod,
    PodJoinRequest,
)
from pods.services import config as pods_config
from pods.services.availability import freshness_line, summary_rows
from pods.services.meeting_presentation import (
    meetings_section,
    next_meeting_lines,
    propose_state,
    propose_url,
    same_week_slot,
    without_near,
)
from pods.services.membership import (
    REREQUEST_COOLDOWN,
    REREQUEST_FINAL,
    REREQUEST_RETRY,
    can_manage,
    pod_url,
    pods_tab_url,
    rerequest_state,
    user_date_label,
)
from pods.services.people import (
    display_name,
    humanize_age,
    is_cohort_participant,
    is_slack_eligible,
    offset_range_label,
    timezone_label,
)
from pods.services.slack import profile_url, suggested_channel_name
from pods.services.suggestions import (
    load_member_availability,
    member_strip,
    required_attendance,
    slots_attendable,
    suggest_slots,
    weekly_overlap_hours,
)

SLOT_DATETIME_FORMAT = '%a, %b %d, %H:%M'

STATE_MEMBER = 'member'
STATE_PENDING = 'pending'
STATE_WAITLISTED = 'waitlisted'
STATE_DECLINED = 'declined'
STATE_OPEN = 'open'
STATE_FULL = 'full'
STATE_CLOSED = 'closed'


@dataclass
class ViewerState:
    key: str
    badge: str
    tone: str
    action_label: str
    action_url: str
    request: object = None
    waitlist_position: int | None = None


def meta_line(pod, count):
    return (
        f'{count} of {pod.max_members} members · '
        f'{pod.meeting_count} meeting{"s" if pod.meeting_count != 1 else ""} · '
        f'{pod.meeting_minutes} min'
    )


def pods_url(course, cohort):
    url = reverse('course_pods', kwargs={'slug': course.slug})
    return f'{url}?cohort={cohort.external_key}' if cohort.external_key else url


def viewer_state(pod, viewer, *, count, is_member, open_request, last_request, can_request):
    url = pod_url(pod)
    if is_member:
        return ViewerState(STATE_MEMBER, 'Your pod', 'success', 'Open pod', url)
    if open_request is not None:
        if open_request.status == REQUEST_STATUS_WAITLISTED:
            position = PodJoinRequest.objects.filter(
                pod_id=pod.pk, status=REQUEST_STATUS_WAITLISTED,
                created_at__lt=open_request.created_at,
            ).count() + 1
            return ViewerState(
                STATE_WAITLISTED, f'On waiting list - #{position}', 'info', 'Open pod', url,
                request=open_request, waitlist_position=position,
            )
        return ViewerState(STATE_PENDING, 'Request sent', 'info', 'Open pod', url, request=open_request)
    if last_request is not None and last_request.status == REQUEST_STATUS_DECLINED:
        return ViewerState(STATE_DECLINED, 'Not accepted', 'muted', 'Open pod', url)
    if pod.status != POD_STATUS_OPEN:
        return ViewerState(STATE_CLOSED, 'Closed', 'muted', 'Open pod', url)
    if count >= pod.max_members:
        label = 'Join waiting list' if can_request else 'Open pod'
        return ViewerState(STATE_FULL, 'Full - waiting list', 'muted', label, f'{url}#request' if can_request else url)
    label = 'Request to join' if can_request else 'Open pod'
    return ViewerState(STATE_OPEN, 'Open', 'neutral', label, f'{url}#request' if can_request else url)


def _requests_by_pod(pods, viewer):
    """Viewer's open request and most recent request per pod."""
    if not getattr(viewer, 'is_authenticated', False):
        return {}, {}
    open_requests, last_requests = {}, {}
    for request in PodJoinRequest.objects.filter(
        user=viewer, pod__in=pods,
    ).order_by('created_at', 'pk'):
        last_requests[request.pod_id] = request
        if request.status in OPEN_REQUEST_STATUSES:
            open_requests[request.pod_id] = request
    return open_requests, last_requests


def fit_line(hours):
    if hours is None:
        return ''
    if hours <= 0:
        return 'No overlap with you yet'
    return f'Overlaps with you {hours} h a week'


def build_pod_rows(cohort, viewer, *, now=None):
    """Rows for the Pods tab, ordered: yours, open by fit, full, closed."""
    now = now or timezone.now()
    pods = list(
        Pod.objects.filter(cohort=cohort)
        .exclude(status=POD_STATUS_ARCHIVED)
        .select_related('owner', 'cohort__course')
        .prefetch_related('memberships__user')
        .order_by('-created_at', '-pk')
    )
    users = {viewer.pk: viewer}
    for pod in pods:
        for membership in pod.memberships.all():
            users.setdefault(membership.user_id, membership.user)
    availability = load_member_availability(users.values())
    viewer_availability = availability.get(viewer.pk)
    open_requests, last_requests = _requests_by_pod(pods, viewer)
    can_request = is_cohort_participant(viewer, cohort)
    own_pods = [pod for pod in pods if any(m.user_id == viewer.pk for m in pod.memberships.all())]
    meeting_lines = next_meeting_lines(own_pods, _viewer_zone(viewer_availability, viewer), now)
    rows = []
    for pod in pods:
        members = [m.user for m in pod.memberships.all()]
        member_ids = {u.pk for u in members}
        count = len(members)
        state = viewer_state(
            pod, viewer, count=count, is_member=viewer.pk in member_ids,
            open_request=open_requests.get(pod.pk), last_request=last_requests.get(pod.pk),
            can_request=can_request,
        )
        others = [availability[u.pk] for u in members if u.pk != viewer.pk]
        hours = None
        if state.key != STATE_MEMBER:
            hours = weekly_overlap_hours(viewer_availability, others, now=now)
        rows.append({
            'pod': pod,
            'count': count,
            'meta': meta_line(pod, count),
            'zones': offset_range_label([availability[u.pk].timezone_name for u in members], now),
            'state': state,
            'fit_hours': hours,
            'fit_line': fit_line(hours),
            'meeting_line': meeting_lines.get(pod.pk, '') if state.key == STATE_MEMBER else '',
        })

    def group(row):
        if row['state'].key == STATE_MEMBER:
            return 0
        if row['pod'].status != POD_STATUS_OPEN:
            return 3
        if row['count'] >= row['pod'].max_members:
            return 2
        return 1

    def sort_key(row):
        fit = row['fit_hours'] if row['fit_hours'] is not None else -1
        return (group(row), -fit, -row['pod'].created_at.timestamp(), -row['pod'].pk)

    rows.sort(key=sort_key)
    return rows, viewer_availability


def pending_count_for_owner(cohort, user):
    """Pending requests on the pods ``user`` owns in ``cohort`` (tab badge)."""
    if not getattr(user, 'is_authenticated', False) or cohort is None:
        return 0
    return PodJoinRequest.objects.filter(
        pod__cohort=cohort, pod__owner=user, status=REQUEST_STATUS_PENDING,
    ).exclude(pod__status=POD_STATUS_ARCHIVED).count()


def _viewer_zone(viewer_availability, viewer):
    if viewer_availability is not None and viewer_availability.timezone_name:
        return viewer_availability.timezone_name
    return 'UTC'


def slot_rows(slots, viewer_zone, members):
    """Template rows for suggested slots.

    ``members`` (the pod's :class:`MemberAvailability` rows in membership
    order) drive the strip, so members without availability are listed as
    ``no availability`` rather than dropped or given a time.
    """
    viewer = SimpleNamespace(preferred_timezone=viewer_zone)
    rows = []
    for slot in slots:
        rows.append({
            'slot': slot,
            'start_label': format_user_datetime(slot.start, viewer, fmt=SLOT_DATETIME_FORMAT),
            'start_iso': slot.start.isoformat(),
            'fit_label': slot.fit_label,
            'fit_tone': slot.fit_tone,
            'strip': member_strip(slot, members),
        })
    return rows


def horizon_label(days):
    """14 -> ``2 weeks``; 7 -> ``week``; 10 -> ``10 days``."""
    if days == 7:
        return 'week'
    if days % 7 == 0:
        return f'{days // 7} weeks'
    return f'{days} days'


def _join_names(names):
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + ' and ' + names[-1]


def suggestions_context(pod, members_availability, viewer_zone, *, now=None):
    considered = [m for m in members_availability if m.has_windows]
    missing = [m for m in members_availability if not m.has_windows]
    needed = required_attendance(len(considered))
    context = {
        'considered_count': len(considered),
        'member_total': len(members_availability),
        'needs_more': needed is None,
        'slots': [],
        'based_on_line': '',
        'no_fit_line': '',
        'raw_slots': [],
    }
    if needed is None:
        return context
    slots = suggest_slots(
        members_availability, pod.meeting_minutes, now=now, all_members=members_availability,
    )
    context['raw_slots'] = slots
    context['slots'] = slot_rows(slots, viewer_zone, members_availability)
    if missing:
        names = [display_name(m.user) for m in missing]
        verb = "hasn't" if len(names) == 1 else "haven't"
        context['based_on_line'] = (
            f'Based on {len(considered)} of {len(members_availability)} members. '
            f'{_join_names(names)} {verb} added availability yet.'
        )
    if not slots:
        context['no_fit_line'] = (
            f'No time fits at least {needed} of you in the next '
            f'{horizon_label(pods_config.suggestion_horizon_days())}. '
            'Ask members to add more windows, or add some yourself.'
        )
    return context


def _add_meeting_actions(pod, suggestions, state, member_avail, viewer_zone, *, is_member, count, now):
    """Issue #1919: ``Propose this time`` on each slot, the leading ``Same
    weekly time`` row, and the line explaining why proposing is blocked."""
    can_propose, blocked = propose_state(pod, state, is_member=is_member, member_count=count)
    same = same_week_slot(pod, state, member_avail, now)
    if same is not None:
        rows = slot_rows([same], viewer_zone, member_avail)
        rows[0]['same_week'] = True
        suggestions['slots'] = rows + slot_rows(
            without_near(suggestions['raw_slots'], same), viewer_zone, member_avail,
        )
    for row in suggestions['slots']:
        row['propose_url'] = propose_url(pod, row['slot'].start) if can_propose else ''
    suggestions['propose_blocked_line'] = blocked
    suggestions['custom_propose_url'] = propose_url(pod) if can_propose else ''


def request_notice(rerequest, viewer):
    """The line under the ``Not accepted`` badge (issue #1927), or ``None``."""
    if rerequest.key == REREQUEST_COOLDOWN:
        return {
            'testid': 'pod-request-blocked',
            'text': (
                'Your request was not accepted. You can ask again on '
                f'{user_date_label(rerequest.available_at, viewer)}.'
            ),
        }
    if rerequest.key == REREQUEST_RETRY:
        return {
            'testid': 'pod-request-retry',
            'text': 'Your last request was not accepted. You can ask once more.',
        }
    if rerequest.key == REREQUEST_FINAL:
        return {
            'testid': 'pod-request-blocked',
            'text': "This pod declined your request twice, so you can't ask to join it again.",
            'link_label': 'Browse other pods',
            'link_suffix': ' or start your own.',
        }
    return None


def build_pod_page(pod, viewer, *, now=None):
    """Context for the member pod page."""
    now = now or timezone.now()
    memberships = list(pod.memberships.select_related('user').order_by('joined_at', 'pk'))
    members = [m.user for m in memberships]
    count = len(members)
    is_member = any(u.pk == viewer.pk for u in members)
    is_staff = bool(viewer.is_staff)
    manage = can_manage(pod, viewer)
    open_requests_qs = list(
        pod.join_requests.filter(status__in=OPEN_REQUEST_STATUSES)
        .select_related('user').order_by('created_at', 'pk')
    )
    # Keep ``viewer`` itself in the map so its loaded profile lands on the
    # request user, not on the membership's copy of the same row.
    users = {viewer.pk: viewer}
    for user in members:
        users.setdefault(user.pk, user)
    for request in open_requests_qs:
        users.setdefault(request.user_id, request.user)
    availability = load_member_availability(users.values())
    member_avail = [availability[u.pk] for u in members]
    viewer_availability = availability.get(viewer.pk)
    viewer_zone = _viewer_zone(viewer_availability, viewer)
    open_requests, last_requests = _requests_by_pod([pod], viewer)
    can_request = is_cohort_participant(viewer, pod.cohort)
    state = viewer_state(
        pod, viewer, count=count, is_member=is_member,
        open_request=open_requests.get(pod.pk), last_request=last_requests.get(pod.pk),
        can_request=can_request,
    )
    rerequest = None
    notice = None
    if not is_member and state.request is None:
        rerequest = rerequest_state(pod, viewer, now=now)
        notice = request_notice(rerequest, viewer)
        if rerequest.key == REREQUEST_RETRY and state.key != STATE_DECLINED:
            # A later withdrawn request: no "last request was not accepted".
            notice = None
        if notice and notice.get('link_label'):
            notice['link_url'] = pods_tab_url(pod.cohort)
    show_private = is_member or is_staff
    viewer_slack_ok = is_slack_eligible(viewer)

    member_rows = []
    for user in members:
        member = availability[user.pk]
        slack_url = ''
        if is_member and viewer_slack_ok and user.pk != viewer.pk and is_slack_eligible(user):
            slack_url = profile_url(user)
        member_rows.append({
            'user_id': user.pk,
            'name': display_name(user),
            'timezone_label': timezone_label(member.timezone_name),
            'has_availability': member.has_windows,
            'is_owner': pod.owner_id == user.pk,
            'is_viewer': user.pk == viewer.pk,
            'slack_url': slack_url,
        })

    suggestions = suggestions_context(pod, member_avail, viewer_zone, now=now) if show_private else None
    raw_slots = suggestions['raw_slots'] if suggestions else []
    meetings = None
    if show_private:
        meetings = meetings_section(
            pod, viewer, member_avail=member_avail, viewer_zone=viewer_zone, raw_slots=raw_slots,
            is_member=is_member, can_manage=manage, now=now,
        )
        _add_meeting_actions(pod, suggestions, meetings['state'], member_avail, viewer_zone,
                             is_member=is_member, count=count, now=now)
    if manage and not raw_slots:
        raw_slots = suggest_slots(member_avail, pod.meeting_minutes, now=now) if len(
            [m for m in member_avail if m.has_windows]) >= 2 else []

    request_rows = []
    if manage:
        waitlist_index = 0
        full = count >= pod.max_members
        ordered = sorted(
            open_requests_qs,
            key=lambda r: (r.status != REQUEST_STATUS_PENDING, r.created_at, r.pk),
        )
        for request in ordered:
            requester = availability[request.user_id]
            position = None
            if request.status == REQUEST_STATUS_WAITLISTED:
                waitlist_index += 1
                position = waitlist_index
            if raw_slots:
                fit = f'Fits {slots_attendable(requester, raw_slots)} of {len(raw_slots)} suggested times'
            elif requester.has_windows:
                fit = fit_line(weekly_overlap_hours(requester, member_avail, now=now)).replace(
                    'with you', 'with the pod',
                ) or 'No suggested times yet'
            else:
                fit = 'No availability yet'
            request_rows.append({
                'request': request,
                'name': display_name(request.user),
                'timezone_label': timezone_label(requester.timezone_name),
                'message': request.message,
                'age': f'Requested {humanize_age(request.created_at, now)}',
                'fit': fit,
                'waitlist_position': position,
                'status_label': f'On waiting list - #{position}' if position else 'Pending',
                'can_approve': not full,
            })

    fit_hours = None
    if not is_member:
        fit_hours = weekly_overlap_hours(viewer_availability, member_avail, now=now)

    slack = None
    if is_member:
        others_below_main = any(
            not is_slack_eligible(u) for u in members if u.pk != viewer.pk
        )
        slack = {
            'channel_url': pod.slack_channel_url,
            'viewer_eligible': viewer_slack_ok,
            'show_callout': not pod.slack_channel_url and count >= 2 and viewer_slack_ok,
            'show_gate': not pod.slack_channel_url and not viewer_slack_ok,
            'others_below_main': others_below_main,
            'viewer_in_workspace': bool(getattr(viewer, 'slack_member', False)),
            'suggested_name': suggested_channel_name(pod),
        }

    profile = getattr(viewer, '_pods_profile', None)
    return {
        'pod': pod,
        'count': count,
        'meta': meta_line(pod, count),
        'owner_name': display_name(pod.owner) if pod.owner_id else '',
        'state': state,
        'is_member': is_member,
        'can_manage': manage,
        'can_request': (
            can_request and not is_member and state.request is None
            and pod.status == POD_STATUS_OPEN
            and not (rerequest and rerequest.blocked)
        ),
        'request_notice': notice,
        'request_label': 'Join waiting list' if count >= pod.max_members else 'Request to join',
        'show_private': show_private,
        'suggestions': suggestions,
        'meetings': meetings,
        'call_link_value': pod.meeting_url if show_private else '',
        'viewer_zone': viewer_zone,
        'member_rows': member_rows,
        'zones': offset_range_label([m.timezone_name for m in member_avail], now),
        'request_rows': request_rows,
        'pod_full': count >= pod.max_members,
        'fit_line': fit_line(fit_hours),
        'slack': slack,
        'viewer_has_availability': bool(viewer_availability and viewer_availability.has_windows),
        'availability_summary': summary_rows(profile) if is_member else [],
        'availability_timezone': timezone_label(viewer_availability.timezone_name) if viewer_availability else '',
        'freshness_line': freshness_line(profile, now) if is_member else '',
    }
