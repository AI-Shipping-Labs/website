"""Studio pages for pods (issue #1918).

Planning -> Pods. Staff can create pods for students paired by external
tooling, edit any pod (including archive and owner changes), add or remove
members by email, and approve or decline requests as an override. Every
write goes through ``pods.services.membership`` so the capacity, waiting
list and leave rules match the member pages and the staff API.
"""

from zoneinfo import ZoneInfo

from django.contrib import messages
from django.db.models import Count, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.services.timezones import build_timezone_options
from content.models import Cohort
from pods.models import (
    MEETING_MINUTES_CHOICES,
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    MEETING_STATUS_PROPOSED,
    MEETING_STATUS_SCHEDULED,
    OPEN_REQUEST_STATUSES,
    POD_SOURCE_STUDIO,
    POD_STATUS_CHOICES,
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_WAITLISTED,
    Pod,
    PodJoinRequest,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services import config as pods_config
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.meeting_rules import meeting_state
from pods.services.people import display_name, humanize_age
from pods.services.stale_requests import is_stale
from pods.services.suggestions import load_member_availability, suggest_slots
from studio.decorators import staff_required
from studio.utils import studio_pagination_context

RESULTS_SESSION_KEY = 'studio_pod_add_results'
RESULT_BUCKET_LABELS = (
    ('added', 'Added'),
    ('not_enrolled', 'Not enrolled'),
    ('unknown_user', 'Unknown user'),
    ('already_member', 'Already a member'),
    ('over_capacity', 'Over capacity'),
)


def _cohort_label(cohort):
    return f'{cohort.course.title} - {cohort.name}'


def _dated_cohorts(active_only=False):
    cohorts = Cohort.objects.filter(mode='cohort').select_related('course').order_by(
        'course__title', 'start_date', 'pk',
    )
    if active_only:
        cohorts = cohorts.filter(is_active=True)
    return cohorts


def _store_results(request, pod, results):
    request.session[RESULTS_SESSION_KEY] = {'pod_id': pod.pk, 'results': results.as_dict()}


def _pop_results(request, pod):
    stored = request.session.pop(RESULTS_SESSION_KEY, None)
    if not stored or stored.get('pod_id') != pod.pk:
        return []
    return [
        {'key': key, 'label': label, 'emails': stored['results'].get(key, [])}
        for key, label in RESULT_BUCKET_LABELS
        if stored['results'].get(key)
    ]


@staff_required
def studio_pod_list(request):
    """List pods with members, request queue, staleness and Slack state."""
    search = (request.GET.get('q') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    cohort_filter = (request.GET.get('cohort') or '').strip()
    now = timezone.now()
    open_filter = Q(join_requests__status__in=OPEN_REQUEST_STATUSES)
    pods = (
        Pod.objects.filter(cohort__isnull=False)
        .select_related('cohort__course', 'owner')
        .annotate(
            member_total=Count('memberships', distinct=True),
            pending_total=Count(
                'join_requests', filter=Q(join_requests__status=REQUEST_STATUS_PENDING), distinct=True,
            ),
            waiting_total=Count(
                'join_requests', filter=Q(join_requests__status=REQUEST_STATUS_WAITLISTED), distinct=True,
            ),
            oldest_open_request=Min('join_requests__created_at', filter=open_filter),
            oldest_pending_request=Min(
                'join_requests__created_at', filter=Q(join_requests__status=REQUEST_STATUS_PENDING),
            ),
            # Issue #1919: held meetings and the next scheduled start.
            held_total=Count('meetings', filter=Q(meetings__status=MEETING_STATUS_HELD), distinct=True),
            next_meeting_at=Min(
                'meetings__starts_at',
                filter=Q(meetings__status=MEETING_STATUS_SCHEDULED, meetings__starts_at__gt=now),
            ),
        )
        .order_by('-created_at', '-pk')
    )
    if search:
        pods = pods.filter(name__icontains=search)
    if status_filter in {value for value, _label in POD_STATUS_CHOICES}:
        pods = pods.filter(status=status_filter)
    if cohort_filter.isdigit():
        pods = pods.filter(cohort_id=int(cohort_filter))
    pager = studio_pagination_context(request, pods)
    stale_days = pods_config.stale_request_days()
    rows = []
    for pod in pager['page'].object_list:
        oldest = pod.oldest_open_request
        rows.append({
            'pod': pod,
            'oldest_age': humanize_age(oldest, now) if oldest else '',
            # Pending only (issue #1927): a waitlisted request cannot be
            # approved until a seat opens, so it never makes a pod stale.
            # Same rule as the daily staff Slack alert.
            'stale': is_stale(pod.oldest_pending_request, now, stale_days),
            'stale_age': humanize_age(pod.oldest_pending_request, now) if pod.oldest_pending_request else '',
        })
    cohort_options = [(str(c.pk), _cohort_label(c)) for c in _dated_cohorts()]
    return render(request, 'studio/pods/list.html', {
        'rows': rows,
        'search': search,
        'status_filter': status_filter,
        'cohort_filter': cohort_filter,
        'filters_active': bool(search or status_filter or cohort_filter),
        'extra_filters': [{
            'name': 'cohort', 'label': 'Cohort', 'all_label': 'All cohorts',
            'value': cohort_filter, 'options': cohort_options,
        }],
        'stale_days': stale_days,
        **pager,
    })


def _form_defaults():
    return {
        'cohort': '', 'name': '', 'purpose': '',
        'max_members': str(pods_config.default_max_members()),
        'meeting_count': str(pods_config.default_meeting_count()),
        'meeting_minutes': str(pods_config.default_meeting_minutes()),
        'owner_email': '', 'member_emails': '',
    }


@staff_required
def studio_pod_create(request):
    """Create a pod in a dated cohort, optionally with an owner and members."""
    form = _form_defaults()
    if request.GET.get('cohort', '').isdigit():
        form['cohort'] = request.GET['cohort']
    error = ''
    if request.method == 'POST':
        form = {key: request.POST.get(key, '') for key in form}
        cohort = None
        if form['cohort'].isdigit():
            cohort = Cohort.objects.select_related('course').filter(pk=int(form['cohort'])).first()
        if cohort is None:
            error = 'Choose a cohort.'
        else:
            try:
                pod, results, created = svc.create_staff_pod(
                    cohort=cohort,
                    data=form,
                    owner_email=form['owner_email'],
                    member_emails=form['member_emails'],
                    actor=request.user,
                    source=POD_SOURCE_STUDIO,
                )
            except svc.PodError as exc:
                error = exc.message
            else:
                _store_results(request, pod, results)
                if created:
                    messages.success(request, f'Pod "{pod.name}" created.')
                else:
                    messages.info(
                        request,
                        f'Pod "{pod.name}" already exists in this cohort; '
                        'new emails were added to it.',
                    )
                return redirect('studio_pod_detail', pod_id=pod.pk)
    return render(request, 'studio/pods/new.html', {
        'form': form,
        'error': error,
        'cohort_options': [(str(c.pk), _cohort_label(c)) for c in _dated_cohorts(active_only=True)],
        'meeting_minute_choices': MEETING_MINUTES_CHOICES,
    }, status=400 if error else 200)


def _member_url(pod):
    return svc.pod_url(pod)


@staff_required
def studio_pod_detail(request, pod_id):
    """Settings form (POST saves), members, requests and suggested times."""
    pod = get_object_or_404(Pod.objects.select_related('cohort__course', 'owner'), pk=pod_id)
    error = ''
    if request.method == 'POST':
        data = {key: request.POST.get(key, '') for key in (
            'name', 'purpose', 'max_members', 'meeting_count', 'meeting_minutes', 'status',
        )}
        call_link_error = ''
        if 'meeting_url' in request.POST:
            try:
                data['meeting_url'] = mtg.clean_call_link(request.POST['meeting_url'])
            except ValueError as exc:
                call_link_error = str(exc)
        owner_raw = request.POST.get('owner', '')
        if owner_raw == '':
            data['owner'] = None
        elif owner_raw.isdigit():
            membership = pod.memberships.select_related('user').filter(user_id=int(owner_raw)).first()
            data['owner'] = membership.user if membership else -1
        try:
            if call_link_error:
                raise svc.PodError(call_link_error, field='meeting_url')
            if data.get('owner') == -1:
                raise svc.PodError('The owner must be a member of the pod.')
            svc.update_pod(pod, data, actor=request.user, staff=True)
        except svc.PodError as exc:
            error = exc.message
        else:
            messages.success(request, 'Pod saved.')
            return redirect('studio_pod_detail', pod_id=pod.pk)
        pod.refresh_from_db()

    memberships = list(pod.memberships.select_related('user', 'added_by').order_by('joined_at', 'pk'))
    requests = list(
        pod.join_requests.filter(status__in=OPEN_REQUEST_STATUSES)
        .select_related('user').order_by('created_at', 'pk')
    )
    users = [m.user for m in memberships]
    availability = load_member_availability(users)
    member_avail = [availability[u.pk] for u in users]
    slots = suggest_slots(member_avail, pod.meeting_minutes, all_members=member_avail)
    now = timezone.now()
    waitlist_index = 0
    request_rows = []
    for join_request in sorted(requests, key=lambda r: (r.status != REQUEST_STATUS_PENDING, r.created_at, r.pk)):
        position = None
        if join_request.status == REQUEST_STATUS_WAITLISTED:
            waitlist_index += 1
            position = waitlist_index
        request_rows.append({
            'request': join_request,
            'position': position,
            'status_label': f'Waiting #{position}' if position else 'Pending',
            'age': humanize_age(join_request.created_at, now),
        })
    slot_rows = [{
        'slot': slot,
        'utc_label': f'{slot.start:%Y-%m-%d %H:%M}–{slot.end:%H:%M} UTC',
        'local': [
            {
                'name': local['member'].user.email,
                'time': slot.start.astimezone(ZoneInfo(local['timezone'])).strftime('%a %H:%M'),
                'timezone': local['timezone'],
            }
            for local in slot.local_starts
        ],
        'missing': [m.user.email for m in slot.missing],
    } for slot in slots]
    return render(request, 'studio/pods/detail.html', {
        'pod': pod,
        'error': error,
        **_meetings_context(pod, users, request.session.pop(SCHEDULE_FORM_SESSION_KEY, None)),
        'activity': _cohort_label(pod.cohort),
        'memberships': memberships,
        'member_rows': [
            {'membership': m, 'timezone': availability[m.user_id].timezone_name,
             'has_windows': availability[m.user_id].has_windows}
            for m in memberships
        ],
        'request_rows': request_rows,
        'pod_full': len(memberships) >= pod.max_members,
        'slot_rows': slot_rows,
        'status_choices': POD_STATUS_CHOICES,
        'meeting_minute_choices': MEETING_MINUTES_CHOICES,
        'add_results': _pop_results(request, pod),
        'member_view_url': _member_url(pod),
        'enabled_for_members': pods_config.pods_enabled_for_course(pod.cohort.course),
    }, status=400 if error else 200)


SCHEDULE_FORM_SESSION_KEY = 'studio_pod_schedule_form'
STUDIO_MEETING_STATUS = {
    MEETING_STATUS_PROPOSED: ('pending', 'Proposed'),
    MEETING_STATUS_SCHEDULED: ('upcoming', 'Scheduled'),
    MEETING_STATUS_HELD: ('completed', 'Held'),
    MEETING_STATUS_CANCELLED: ('cancelled', 'Cancelled'),
}


def _meetings_context(pod, users, schedule_form):
    """Issue #1919: the Studio Meetings table and the Schedule meeting form."""
    now = timezone.now()
    state = meeting_state(
        pod, now, meetings=list(PodMeeting.objects.filter(pod=pod).select_related('moved_by')),
    )
    names = {u.pk: display_name(u) for u in users}
    responses = {}
    for meeting_id, user_id, response in PodMeetingResponse.objects.filter(
        meeting__pod=pod,
    ).values_list('meeting_id', 'user_id', 'response'):
        responses.setdefault(meeting_id, {})[user_id] = response
    rows = []
    for meeting in state.meetings:
        answers = responses.get(meeting.pk, {})
        if state.is_expired(meeting):
            badge = ('expired', 'Not confirmed')
        else:
            badge = STUDIO_MEETING_STATUS.get(meeting.status, ('draft', meeting.status))
        if meeting.status == MEETING_STATUS_PROPOSED:
            going = [names[uid] for uid, r in answers.items() if r == MEETING_RESPONSE_GOING and uid in names]
        else:
            going = [name for uid, name in names.items() if answers.get(uid) != MEETING_RESPONSE_CANT]
        rows.append({
            'meeting': meeting,
            'number': state.numbers.get(meeting.pk),
            'status_key': badge[0],
            'status_label': badge[1],
            'going': going,
            'cant': [names[uid] for uid, r in answers.items() if r == MEETING_RESPONSE_CANT and uid in names],
            'moved_by': display_name(meeting.moved_by) if meeting.moved_by_id else '',
            'series_short': str(meeting.series_id)[:8] if meeting.series_id else '',
            # Same rule as the member page and ``set_meeting_status`` (issue #1934).
            'can_hold': meeting.status == MEETING_STATUS_SCHEDULED and meeting.starts_at <= now,
            'can_cancel': meeting.status != MEETING_STATUS_CANCELLED,
        })
    form = schedule_form or {}
    return {
        'meeting_rows': rows,
        'meetings_used': state.used,
        'meetings_held': state.held,
        'schedule_form': {
            'date': form.get('date', ''),
            'time': form.get('time', ''),
            'timezone': form.get('timezone', 'UTC'),
            'repeat_weekly': form.get('repeat_weekly', False),
            'error': form.get('error', ''),
        },
        'timezone_options': build_timezone_options(),
    }


@staff_required
@require_POST
def studio_pod_meeting_schedule(request, pod_id):
    """Schedule agreed meetings for a pod (one, or weekly for the rest)."""
    pod = get_object_or_404(Pod.objects.select_related('cohort__course'), pk=pod_id)
    form = {
        'date': request.POST.get('date', ''),
        'time': request.POST.get('time', ''),
        'timezone': request.POST.get('timezone', '') or 'UTC',
        'repeat_weekly': bool(request.POST.get('repeat_weekly')),
    }
    try:
        zone_name = mtg.valid_zone(form['timezone'])
        start = mtg.parse_local(form['date'], form['time'], zone_name)
        created = mtg.schedule_meetings(
            pod, request.user, start, zone_name=zone_name, repeat_weekly=form['repeat_weekly'],
            created_via=POD_SOURCE_STUDIO,
        )
    except svc.PodError as exc:
        request.session[SCHEDULE_FORM_SESSION_KEY] = {**form, 'error': exc.message}
    else:
        noun = 'meeting' if len(created) == 1 else 'meetings'
        messages.success(request, f'Scheduled {len(created)} {noun}.')
    return redirect(f'{reverse("studio_pod_detail", kwargs={"pod_id": pod.pk})}#meetings')


@staff_required
@require_POST
def studio_pod_meeting_status(request, pod_id, meeting_id):
    """Row actions: ``status=held`` or ``status=cancelled`` (staff override)."""
    pod = get_object_or_404(Pod, pk=pod_id)
    meeting = get_object_or_404(PodMeeting, pk=meeting_id, pod=pod)
    status = request.POST.get('status', '')
    if status not in (MEETING_STATUS_HELD, MEETING_STATUS_CANCELLED):
        messages.error(request, 'Choose held or cancelled.')
    else:
        try:
            mtg.set_meeting_status(meeting, request.user, status)
        except svc.PodError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, 'Meeting marked as held.' if status == MEETING_STATUS_HELD else 'Meeting cancelled.')
    return redirect(f'{reverse("studio_pod_detail", kwargs={"pod_id": pod.pk})}#meetings')


@staff_required
@require_POST
def studio_pod_member_add(request, pod_id):
    pod = get_object_or_404(Pod.objects.select_related('cohort__course'), pk=pod_id)
    results = svc.add_members_by_email(
        pod, request.POST.get('emails', ''), actor=request.user, source='staff',
    )
    _store_results(request, pod, results)
    return redirect('studio_pod_detail', pod_id=pod.pk)


@staff_required
@require_POST
def studio_pod_member_remove(request, pod_id, user_id):
    pod = get_object_or_404(Pod.objects.select_related('cohort__course'), pk=pod_id)
    membership = get_object_or_404(pod.memberships.select_related('user'), user_id=user_id)
    svc.remove_member(pod, membership.user, actor=request.user)
    messages.success(request, f'Removed {membership.user.email}.')
    return redirect('studio_pod_detail', pod_id=pod.pk)


def _decide(request, pod_id, request_id, decide, verb):
    pod = get_object_or_404(Pod, pk=pod_id)
    join_request = get_object_or_404(PodJoinRequest.objects.select_related('user'), pk=request_id, pod=pod)
    try:
        decide(join_request, request.user)
    except svc.PodError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f'{verb} {join_request.user.email}.')
    return redirect(f'{reverse("studio_pod_detail", kwargs={"pod_id": pod.pk})}#requests')


@staff_required
@require_POST
def studio_pod_request_approve(request, pod_id, request_id):
    return _decide(request, pod_id, request_id, svc.approve_request, 'Approved')


@staff_required
@require_POST
def studio_pod_request_decline(request, pod_id, request_id):
    return _decide(request, pod_id, request_id, svc.decline_request, 'Declined')
