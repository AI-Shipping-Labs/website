"""Member pod meeting pages and actions (issue #1919).

Same access rules as the pod page: pods switched off for the course, users
outside the cohort and non-members get 404; anonymous users go to login.
Staff may move, cancel and record meetings and set the call link as an
operator; proposing and answering are for pod members. A meeting id from
another pod is a 404. Writes are session POSTs that redirect back with a
Django message.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.services.timezones import get_timezone_label
from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    MEETING_STATUS_PROPOSED,
    MEETING_STATUS_SCHEDULED,
    PodMeeting,
)
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.meeting_presentation import propose_state, propose_url
from pods.services.meeting_rules import meeting_state
from pods.services.suggestions import (
    evaluate_slot,
    load_member_availability,
    member_strip,
    required_attendance,
    suggest_slots,
)
from pods.services.tab import course_header_context
from pods.views.member import LOGIN_URL, _enabled_course, _pod_for_viewer

MSG_PROPOSED = 'Time proposed. It is confirmed once {needed} of you can make it.'
MSG_CONFIRMED = 'Meeting confirmed.'
MSG_GOING = 'Marked that you can make it.'
MSG_CANT = "Marked that you can't make it. The pod sees the next best time."
MSG_MOVED = 'Meeting moved.'
MSG_CANCELLED = 'Meeting cancelled.'
MSG_HELD = 'Marked as held.'
MSG_NOT_HELD = "Marked that it didn't happen."
MSG_LINK_SAVED = 'Call link saved.'
MSG_LINK_REMOVED = 'Call link removed.'
CALL_LINK_ERROR_KEY = 'pod_call_link_error_{pod_id}'


def _meeting_pod(request, slug, pod_id, *, allow_staff):
    """The pod for a member (or, when ``allow_staff``, staff), else 404."""
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
    member = svc.is_member(pod, request.user)
    if not member and not (allow_staff and request.user.is_staff):
        raise Http404('Pod not found')
    return course, pod


def _meeting(pod, meeting_id):
    return get_object_or_404(PodMeeting, pk=meeting_id, pod=pod)


def _back(pod):
    return redirect(svc.pod_url(pod))


def _error(request, pod, exc):
    messages.error(request, exc.message)
    return _back(pod)


def _members(pod):
    users = [m.user for m in pod.memberships.select_related('user').order_by('joined_at', 'pk')]
    availability = load_member_availability(users)
    return [availability[u.pk] for u in users]


def _start_from_post(request, zone_name):
    """A suggested-slot ISO ``start`` wins; else the custom date and time."""
    raw = (request.POST.get('start') or '').strip()
    choice = (request.POST.get('choice') or '').strip()
    if choice and choice != 'custom':
        raw = choice
    if raw and choice != 'custom':
        return mtg.parse_instant(raw)
    return mtg.parse_local(request.POST.get('date'), request.POST.get('time'), zone_name)


def _form_base(request, course, pod, zone_name):
    context = course_header_context(course, request.user, pod.cohort)
    context.update({
        'pod': pod,
        'pod_url': svc.pod_url(pod),
        'viewer_zone': zone_name,
        'zone_line': f'Times are in {get_timezone_label(zone_name) or zone_name}',
        'error': '',
        'date_value': request.POST.get('date', ''),
        'time_value': request.POST.get('time', ''),
    })
    return context


def _slot_context(pod, start, members, zone_name):
    slot = evaluate_slot(members, start, pod.meeting_minutes, all_members=members)
    return {
        'start_iso': start.isoformat(),
        'start_label': mtg.format_in_zone(start, zone_name),
        'fit_label': slot.fit_label,
        'fit_tone': slot.fit_tone,
        'strip': member_strip(slot, members),
    }


@login_required(login_url=LOGIN_URL)
def meeting_new(request, slug, pod_id):
    """GET/POST ``.../pods/<id>/meetings/new`` -- confirm a proposal."""
    course, pod = _meeting_pod(request, slug, pod_id, allow_staff=False)
    zone_name = mtg.zone_for(request.user)
    members = _members(pod)
    state = meeting_state(pod)
    remaining = pod.meeting_count - state.used
    if request.method == 'GET':
        can_propose, blocked = propose_state(pod, state, is_member=True, member_count=len(members))
        if not can_propose:
            messages.error(request, blocked or mtg.MSG_NEEDS_TWO)
            return _back(pod)
    context = _form_base(request, course, pod, zone_name)
    raw_start = (request.POST.get('start') if request.method == 'POST' else request.GET.get('start')) or ''
    slot = None
    if raw_start.strip():
        try:
            slot = _slot_context(pod, mtg.parse_instant(raw_start), members, zone_name)
        except svc.PodError:
            slot = None
    repeat_checked = remaining >= 2
    if request.method == 'POST':
        repeat_checked = remaining >= 2 and bool(request.POST.get('repeat_weekly'))
        try:
            start = _start_from_post(request, zone_name)
            mtg.propose_meeting(pod, request.user, start, repeat_weekly=repeat_checked, zone_name=zone_name)
        except svc.PodError as exc:
            context['error'] = exc.message
        else:
            needed = required_attendance(len(members)) or 2
            messages.success(request, MSG_PROPOSED.format(needed=needed))
            return _back(pod)
    context.update({
        'mode': 'new',
        'heading': 'Propose a meeting time',
        'submit_label': 'Propose time',
        'slot': slot,
        'remaining': remaining,
        'show_repeat': remaining >= 2,
        'repeat_label': f'Repeat weekly for the remaining {remaining} meetings',
        'repeat_checked': repeat_checked,
        'form_action': propose_url(pod),
    })
    return render(request, 'pods/meeting_form.html', context, status=400 if context['error'] else 200)


def _later_series_meetings(meeting, state):
    if meeting.status != MEETING_STATUS_SCHEDULED or not meeting.series_id:
        return []
    return [
        m for m in state.series(meeting.series_id)
        if m.pk != meeting.pk and state.is_live(m) and m.status == MEETING_STATUS_SCHEDULED
        and m.starts_at > meeting.starts_at
    ]


@login_required(login_url=LOGIN_URL)
def meeting_move(request, slug, pod_id, meeting_id):
    """GET/POST ``.../meetings/<id>/move`` -- the Change time form.

    A POST with only ``start`` (the one-tap ``Move to`` offer) moves this
    meeting alone and returns to the pod page.
    """
    course, pod = _meeting_pod(request, slug, pod_id, allow_staff=True)
    meeting = _meeting(pod, meeting_id)
    zone_name = mtg.zone_for(request.user)
    one_tap = request.method == 'POST' and 'choice' not in request.POST and 'date' not in request.POST
    if one_tap:
        try:
            mtg.move_meeting(meeting, request.user, mtg.parse_instant(request.POST.get('start')), zone_name=zone_name)
        except svc.PodError as exc:
            return _error(request, pod, exc)
        messages.success(request, MSG_MOVED)
        return _back(pod)
    state = meeting_state(pod)
    locked = meeting.status in (MEETING_STATUS_HELD, MEETING_STATUS_CANCELLED) or state.is_expired(meeting)
    if locked or meeting.starts_at <= state.now:
        message = mtg.MSG_LOCKED if locked else mtg.MSG_HAPPENED
        messages.error(request, message)
        return _back(pod)
    members = _members(pod)
    context = _form_base(request, course, pod, zone_name)
    show_move_later = bool(_later_series_meetings(meeting, state))
    move_later = show_move_later and bool(request.POST.get('move_later'))
    choice = request.POST.get('choice', '') if request.method == 'POST' else ''
    if request.method == 'POST':
        try:
            start = _start_from_post(request, zone_name)
            mtg.move_meeting(meeting, request.user, start, zone_name=zone_name, move_later=move_later)
        except svc.PodError as exc:
            context['error'] = exc.message
        else:
            messages.success(request, MSG_MOVED)
            return _back(pod)
    slots = suggest_slots(members, pod.meeting_minutes, all_members=members)
    choices = [
        {
            'value': slot.start.isoformat(),
            'label': mtg.format_in_zone(slot.start, zone_name),
            'fit_label': slot.fit_label,
            'fit_tone': slot.fit_tone,
            'checked': choice == slot.start.isoformat(),
        }
        for slot in slots
        if slot.start != meeting.starts_at
    ]
    context.update({
        'mode': 'move',
        'heading': 'Change meeting time',
        'submit_label': 'Move meeting',
        'meeting': meeting,
        'current_label': mtg.format_in_zone(meeting.starts_at, zone_name),
        'choices': choices,
        'custom_checked': choice == 'custom' or not choices,
        'show_move_later': show_move_later,
        'move_later_checked': move_later,
        'form_action': request.path,
    })
    return render(request, 'pods/meeting_form.html', context, status=400 if context['error'] else 200)


@require_POST
@login_required(login_url=LOGIN_URL)
def meeting_respond(request, slug, pod_id, meeting_id):
    _course, pod = _meeting_pod(request, slug, pod_id, allow_staff=False)
    meeting = _meeting(pod, meeting_id)
    response = request.POST.get('response', '')
    try:
        agreed = mtg.respond_to_meeting(meeting, request.user, response)
    except svc.PodError as exc:
        return _error(request, pod, exc)
    if response == MEETING_RESPONSE_CANT:
        messages.success(request, MSG_CANT)
    else:
        messages.success(request, MSG_CONFIRMED if agreed else MSG_GOING)
    return _back(pod)


@require_POST
@login_required(login_url=LOGIN_URL)
def meeting_cancel(request, slug, pod_id, meeting_id):
    """Cancel; with ``then_propose`` (the proposal's next best time) open the
    confirm page for that time right after."""
    _course, pod = _meeting_pod(request, slug, pod_id, allow_staff=True)
    meeting = _meeting(pod, meeting_id)
    then_propose = (request.POST.get('then_propose') or '').strip()
    try:
        next_start = mtg.parse_instant(then_propose) if then_propose else None
        if next_start is not None and meeting.status != MEETING_STATUS_PROPOSED:
            next_start = None
        mtg.cancel_meeting(meeting, request.user)
    except svc.PodError as exc:
        return _error(request, pod, exc)
    messages.success(request, MSG_CANCELLED)
    if next_start is not None:
        return redirect(propose_url(pod, next_start))
    return _back(pod)


@require_POST
@login_required(login_url=LOGIN_URL)
def meeting_held(request, slug, pod_id, meeting_id):
    _course, pod = _meeting_pod(request, slug, pod_id, allow_staff=True)
    meeting = _meeting(pod, meeting_id)
    outcome = request.POST.get('outcome', '')
    try:
        mtg.record_outcome(meeting, request.user, outcome)
    except svc.PodError as exc:
        return _error(request, pod, exc)
    messages.success(request, MSG_HELD if outcome == mtg.OUTCOME_HELD else MSG_NOT_HELD)
    return _back(pod)


@require_POST
@login_required(login_url=LOGIN_URL)
def pod_call_link(request, slug, pod_id):
    """Set or clear the pod call link (members and staff)."""
    _course, pod = _meeting_pod(request, slug, pod_id, allow_staff=True)
    try:
        value = mtg.set_call_link(pod, request.user, request.POST.get('meeting_url', ''))
    except svc.PodError as exc:
        request.session[CALL_LINK_ERROR_KEY.format(pod_id=pod.pk)] = {
            'message': exc.message, 'value': request.POST.get('meeting_url', '')[:600],
        }
        return redirect(f'{svc.pod_url(pod)}#meetings')
    messages.success(request, MSG_LINK_SAVED if value else MSG_LINK_REMOVED)
    return redirect(f'{svc.pod_url(pod)}#meetings')
