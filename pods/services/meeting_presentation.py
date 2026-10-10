"""View models for pod meetings on member surfaces (issue #1919).

Everything the Meetings section, the confirm and move pages, the Pods tab
and the dashboard need is computed here. Member surfaces never show email
addresses: people are named with ``people.display_name``. Every visible
time goes through ``format_user_datetime`` with a zone token.
"""

from datetime import timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.urls import reverse

from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    MEETING_STATUS_PROPOSED,
    MEETING_STATUS_SCHEDULED,
    POD_STATUS_ARCHIVED,
    Pod,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services import config as pods_config
from pods.services.meeting_rules import (
    meeting_end,
    meeting_state,
    members_without_answer,
    proposal_waiting_since,
)
from pods.services.meetings import (
    JOIN_WINDOW,
    MAX_AHEAD,
    call_link_label,
    format_in_zone,
    series_label,
    weekly_start,
    zone_for,
)
from pods.services.membership import pod_url
from pods.services.people import city_from_zone, display_name
from pods.services.suggestions import (
    MIN_WEEKLY_GAP_MINUTES,
    SUGGESTION_LEAD,
    evaluate_slot,
    make_slot,
    minute_of_week,
    ranked_candidates,
    required_attendance,
    weekly_gap,
)

PAST_LIMIT = 10
SAME_WEEK_SEARCH_WEEKS = 52
NEXT_BEST_GAP = timedelta(minutes=60)
# The next best time stays within a week either side of the meeting (issue #1934).
NEXT_BEST_WINDOW = timedelta(days=7)
DASHBOARD_DAYS = 7
MSG_ALL_DONE = 'All meetings done. Raise the number of meetings to keep going.'
MSG_PROPOSAL_OPEN = 'Confirm or cancel the proposed time before proposing another.'
DASHBOARD_NEEDS_ANSWER = 'Needs your answer'


def msg_all_planned(meeting_count):
    return f'All {meeting_count} meetings are planned.'


def msg_rule(needed):
    """The confirmation rule on the propose and Change time pages."""
    return f'Confirmed once {needed} of you can make it.'


def msg_no_offer(needed):
    return f'No other time within a week of this meeting fits at least {needed} of you.'


def _compact_date(value, zone_name):
    """``Oct 9`` in ``zone_name`` (``member_compact_date`` vocabulary)."""
    local = value.astimezone(ZoneInfo(zone_name))
    return f'{local:%b} {local.day}'


def meetings_url(pod, name, **kwargs):
    return reverse(name, kwargs={'slug': pod.cohort.course.slug, 'pod_id': pod.pk, **kwargs})


def propose_url(pod, start=None):
    url = meetings_url(pod, 'pod_meeting_new')
    return f'{url}?{urlencode({"start": start.isoformat()})}' if start is not None else url


def _member_zone(member):
    return member.timezone_name or 'UTC'


def meeting_strip(start, member_avail):
    """``Anna K. 18:00 Berlin`` for every member, in their own zone."""
    rows = []
    for member in member_avail:
        zone = _member_zone(member)
        rows.append({
            'name': display_name(member.user),
            'time': start.astimezone(ZoneInfo(zone)).strftime('%H:%M'),
            'city': city_from_zone(zone),
        })
    return rows


def _offset(value, zone_name):
    return value.astimezone(ZoneInfo(zone_name)).utcoffset()


def _gap(value, zone_name, anchor):
    return _offset(value, zone_name) - _offset(value, anchor)


def dst_lines(meeting, state, member_avail):
    """Clock-change notes for one series meeting (issues #1919, #1934).

    A member's local time shifts against the meeting's anchor zone only when
    the two zones change clocks on different dates. Compare the offset gap
    between the member's zone and the anchor zone at this meeting with the
    gap at the series' first meeting; moves change both sides, so a moved
    meeting never reads as a clock change.

    - Lasting shift: every later live meeting of the series keeps this
      meeting's gap (and there is at least one). Only the first meeting
      with the new gap says ``Clock change: 13:00 for Mike K. from this
      week (was 12:00).``; later ones, the last included, say nothing.
    - Temporary shift (a later meeting returns to another gap, or this is
      the series' last meeting): ``Clock change: 13:00 for Mike K. this
      week (usually 12:00).``
    """
    if not meeting.series_id:
        return []
    series = state.series(meeting.series_id)
    first = series[0] if series else None
    if first is None or first.pk == meeting.pk:
        return []
    live = [m for m in series if state.is_live(m) and m.pk != meeting.pk]
    later = [m for m in live if m.starts_at > meeting.starts_at]
    earlier = [m for m in live if m.starts_at < meeting.starts_at]
    previous = earlier[-1] if earlier else first
    anchor = meeting.timezone or 'UTC'
    lines = []
    for member in member_avail:
        zone = _member_zone(member)
        now_gap = _gap(meeting.starts_at, zone, anchor)
        first_gap = _gap(first.starts_at, zone, anchor)
        if now_gap == first_gap:
            continue
        local = meeting.starts_at.astimezone(ZoneInfo(zone))
        name = display_name(member.user)
        keeps_gap = all(_gap(m.starts_at, zone, anchor) == now_gap for m in later)
        previous_gap = _gap(previous.starts_at, zone, anchor)
        if keeps_gap and previous_gap == now_gap:
            # A lasting shift that an earlier meeting already announced.
            continue
        if keeps_gap and later:
            was = local - (now_gap - previous_gap)
            lines.append(f'Clock change: {local:%H:%M} for {name} from this week (was {was:%H:%M}).')
            continue
        usual = local - (now_gap - first_gap)
        lines.append(f'Clock change: {local:%H:%M} for {name} this week (usually {usual:%H:%M}).')
    return lines


def _going_count(head, member_ids, responses):
    return sum(
        1 for r in responses.get(head.pk, ())
        if r.response == MEETING_RESPONSE_GOING and r.user_id in member_ids
    )


def _needed_for_offer(member_avail):
    considered = [m for m in member_avail if m.has_windows]
    return required_attendance(len(considered)) or required_attendance(len(member_avail)) or 2


def _neighbours(meeting, group, state):
    """The previous and next live pod meetings around ``meeting``.

    Any live meeting counts (one-off or series), except the meeting's own
    proposal group, so an offer never changes meeting numbers.
    """
    group_ids = {m.pk for m in group}
    others = [m for m in state.meetings if m.pk not in group_ids and state.is_live(m)]
    later = [m for m in others if m.starts_at > meeting.starts_at]
    earlier = [m for m in others if m.starts_at < meeting.starts_at]
    return (earlier[-1] if earlier else None), (later[0] if later else None)


def next_best_slot(meeting, group, state, member_avail, *, now):
    """The time offered after ``Can't make it`` (issue #1934), or ``None``.

    Candidates: every 15-minute start within 7 days either side of the
    meeting (and from 12 hours to 6 months from now) that the pod's
    required attendance can make for the whole meeting. Skipped: starts
    within 60 minutes of the meeting, overlaps with another live meeting,
    and starts at or before the previous or at or after the next live pod
    meeting. Choice, first key wins: same Monday-Sunday week in the
    meeting's timezone, fewest calendar days away, best fit (the suggestion
    rank), closest start, earlier start.
    """
    minutes = meeting.duration_minutes
    first = max(meeting.starts_at - NEXT_BEST_WINDOW, now + SUGGESTION_LEAD)
    last = min(meeting.starts_at + NEXT_BEST_WINDOW, now + MAX_AHEAD)
    if first > last:
        return None
    previous, following = _neighbours(meeting, group, state)
    group_ids = {m.pk for m in group}
    blockers = [m for m in state.meetings if m.pk not in group_ids and state.is_live(m)]
    zone = ZoneInfo(meeting.timezone or 'UTC')
    meeting_day = meeting.starts_at.astimezone(zone).date()
    meeting_week = meeting_day.isocalendar()[:2]
    best = None
    for rank, start, statuses in ranked_candidates(
        member_avail, minutes, first, last + timedelta(minutes=minutes),
    ):
        if abs(start - meeting.starts_at) < NEXT_BEST_GAP:
            continue
        if following is not None and start >= following.starts_at:
            continue
        if previous is not None and start <= previous.starts_at:
            continue
        end = start + timedelta(minutes=minutes)
        if any(other.starts_at < end and start < meeting_end(other) for other in blockers):
            continue
        day = start.astimezone(zone).date()
        key = (
            day.isocalendar()[:2] != meeting_week,
            abs((day - meeting_day).days),
            rank[:3],
            abs(start - meeting.starts_at),
            start,
        )
        if best is None or key < best[0]:
            best = (key, start, statuses)
    if best is None:
        return None
    return make_slot(best[1], minutes, best[2], member_avail)


def _waiting_line(head, members, responses, viewer, viewer_zone):
    """``Waiting on you and Mike K. since Oct 7.``, or ``''`` when everyone answered."""
    waiting = members_without_answer(head, members, responses)
    if not waiting:
        return ''
    names = ['you' for u in waiting if u.pk == viewer.pk] + [display_name(u) for u in waiting if u.pk != viewer.pk]
    who = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
    return f'Waiting on {who} since {_compact_date(proposal_waiting_since(head), viewer_zone)}.'


def _badge(meeting, state, going, needed, now):
    if state.is_expired(meeting):
        return 'Not confirmed', 'muted'
    if meeting.status == MEETING_STATUS_PROPOSED:
        return f'Proposed - {going} of {needed} needed', 'info'
    if meeting.status == MEETING_STATUS_HELD:
        return 'Held', 'success'
    if meeting.status == MEETING_STATUS_CANCELLED:
        return 'Cancelled', 'muted'
    if meeting.starts_at - JOIN_WINDOW <= now < meeting_end(meeting):
        return 'Starting now', 'success'
    return 'Confirmed', 'success'


def build_meeting_rows(pod, viewer, *, member_avail, viewer_zone, is_member, now, state=None):
    """Upcoming rows (soonest first) then past rows (newest first, last 10)."""
    state = state or meeting_state(pod, now)
    member_ids = {m.user.pk for m in member_avail}
    needed = required_attendance(len(member_avail)) or 2
    responses = {}
    meeting_ids = [m.pk for m in state.meetings]
    for response in PodMeetingResponse.objects.filter(meeting_id__in=meeting_ids).order_by('pk'):
        responses.setdefault(response.meeting_id, []).append(response)
    names = {m.user.pk: display_name(m.user) for m in member_avail}
    movers = {m.user.pk: m.user for m in member_avail}
    member_users = [m.user for m in member_avail]

    groups = []
    seen_series = set()
    for meeting in state.meetings:
        if meeting.status == MEETING_STATUS_PROPOSED and meeting.series_id:
            if meeting.series_id in seen_series:
                continue
            seen_series.add(meeting.series_id)
            group = [m for m in state.meetings if m.status == MEETING_STATUS_PROPOSED and m.series_id == meeting.series_id]
        else:
            group = [meeting]
        groups.append(group)

    offer_needed = _needed_for_offer(member_avail)
    upcoming, past = [], []
    for group in groups:
        head = group[0]
        expired = state.is_expired(head)
        live_status = head.status in (MEETING_STATUS_PROPOSED, MEETING_STATUS_SCHEDULED) and not expired
        started = head.starts_at <= now
        in_window = head.starts_at - JOIN_WINDOW <= now < meeting_end(head)
        head_responses = responses.get(head.pk, [])
        viewer_response = next((r.response for r in head_responses if r.user_id == viewer.pk), '')
        cant_names = [names[r.user_id] for r in head_responses if r.response == MEETING_RESPONSE_CANT and r.user_id in names]
        going = _going_count(head, member_ids, responses)
        badge, tone = _badge(head, state, going, needed, now)
        number = state.numbers.get(head.pk)
        is_series_proposal = head.status == MEETING_STATUS_PROPOSED and len(group) > 1
        if is_series_proposal:
            title = f'Proposed: {series_label(head, len(group), viewer_zone)} from {_compact_date(head.starts_at, viewer_zone)}'
        elif number:
            title = f'Meeting {number} of {pod.meeting_count}'
        else:
            title = 'Meeting'
        moved_line = ''
        if head.moved_at and head.moved_by_id:
            mover = movers.get(head.moved_by_id) or head.moved_by
            moved_line = f'Moved by {display_name(mover)} on {_compact_date(head.moved_at, viewer_zone)}'
        row = {
            'meeting': head,
            'group_size': len(group),
            'title': title,
            'time_label': format_in_zone(head.starts_at, viewer_zone),
            'start_iso': head.starts_at.isoformat(),
            'duration': head.duration_minutes,
            'badge': badge,
            'tone': tone,
            'strip': meeting_strip(head.starts_at, member_avail) if live_status or head.status == MEETING_STATUS_HELD else [],
            'proposed_by': display_name(head.proposed_by) if head.status == MEETING_STATUS_PROPOSED and head.proposed_by_id else '',
            'waiting_line': (
                _waiting_line(head, member_users, head_responses, viewer, viewer_zone)
                if live_status and head.status == MEETING_STATUS_PROPOSED else ''
            ),
            'moved_line': moved_line,
            'cant_names': ', '.join(cant_names) if live_status else '',
            'dst_lines': dst_lines(head, state, member_avail) if live_status else [],
            'offer': None,
            'no_offer_line': '',
            'join_url': '',
            'call_link_url': '',
            'call_link_label': '',
        }
        # Join call only once the time is agreed; the plain call-link meta
        # goes on the soonest live, not-started row (set after sorting).
        if live_status and pod.meeting_url and in_window and head.status == MEETING_STATUS_SCHEDULED:
            row['join_url'] = pod.meeting_url
        row['awaiting_start'] = live_status and not started
        actions = {
            'going': '', 'cant': False, 'change': False, 'cancel': '', 'held': False, 'held_primary': False,
        }
        if live_status and not started:
            if head.status == MEETING_STATUS_PROPOSED:
                if is_member and viewer_response != MEETING_RESPONSE_GOING:
                    actions['going'] = 'I can make it'
                actions['cant'] = is_member and viewer_response != MEETING_RESPONSE_CANT
                actions['cancel'] = 'plain'
            else:
                if is_member and viewer_response == MEETING_RESPONSE_CANT:
                    actions['going'] = 'I can make it after all'
                actions['cant'] = is_member and viewer_response != MEETING_RESPONSE_CANT
                actions['cancel'] = 'confirm'
            actions['change'] = True
            if cant_names:
                slot = next_best_slot(head, group, state, member_avail, now=now)
                if slot is not None:
                    short = format_in_zone(slot.start, viewer_zone).rsplit(' ', 1)[0]
                    row['offer'] = {
                        'label': format_in_zone(slot.start, viewer_zone),
                        'fit_label': slot.fit_label,
                        'fit_tone': slot.fit_tone,
                        'start_iso': slot.start.isoformat(),
                        'button': f'Move to {short}' if head.status == MEETING_STATUS_SCHEDULED else f'Propose {short} instead',
                        'propose': head.status == MEETING_STATUS_PROPOSED,
                        # One primary action per row: Join call or I can make it win.
                        'primary': not row['join_url'] and not (
                            head.status == MEETING_STATUS_PROPOSED and actions['going']
                        ),
                    }
                else:
                    row['no_offer_line'] = msg_no_offer(offer_needed)
        elif head.status == MEETING_STATUS_SCHEDULED and started:
            actions['held'] = True
            actions['held_primary'] = not row['join_url']
        row['actions'] = actions
        row['has_actions'] = bool(
            row['join_url'] or row['offer'] or actions['going'] or actions['cant'] or actions['change']
            or actions['cancel'] or actions['held']
        )
        is_upcoming = not expired and (meeting_end(head) > now) and head.status != MEETING_STATUS_HELD
        (upcoming if is_upcoming else past).append(row)
    upcoming.sort(key=lambda r: (r['meeting'].starts_at, r['meeting'].pk))
    soonest = next((r for r in upcoming if r['awaiting_start']), None)
    if soonest is not None and pod.meeting_url and not soonest['join_url']:
        soonest['call_link_url'] = pod.meeting_url
        soonest['call_link_label'] = call_link_label(pod.meeting_url)
    past.sort(key=lambda r: (r['meeting'].starts_at, r['meeting'].pk), reverse=True)
    return upcoming + past[:PAST_LIMIT]


def same_week_slot(pod, state, member_avail, now):
    """``Same time next week``: the next weekly repeat of the latest agreed
    meeting, at least 12 hours away and clear of every live meeting."""
    latest = state.latest_agreed()
    if latest is None or pod.meeting_count - state.used < 1:
        return None
    zone = latest.timezone or 'UTC'
    for week in range(1, SAME_WEEK_SEARCH_WEEKS + 1):
        candidate = weekly_start(latest.starts_at, zone, week)
        if candidate < now + SUGGESTION_LEAD:
            continue
        end = candidate + timedelta(minutes=pod.meeting_minutes)
        if any(state.is_live(m) and m.starts_at < end and candidate < meeting_end(m) for m in state.meetings):
            continue
        return evaluate_slot(member_avail, candidate, pod.meeting_minutes, all_members=member_avail)
    return None


def without_near(slots, anchor):
    """Drop ranked slots within 60 minutes of ``anchor`` on the weekly cycle."""
    if anchor is None:
        return list(slots)
    weekly = minute_of_week(anchor.start)
    return [s for s in slots if weekly_gap(minute_of_week(s.start), weekly) >= MIN_WEEKLY_GAP_MINUTES]


def propose_state(pod, state, *, is_member, member_count):
    """``(can_propose, blocked_line)`` for the Suggested times section."""
    if not is_member or member_count < 2 or pod.status == POD_STATUS_ARCHIVED:
        return False, ''
    if state.has_open_proposal:
        return False, MSG_PROPOSAL_OPEN
    if state.used >= pod.meeting_count:
        return False, msg_all_planned(pod.meeting_count)
    return True, ''


def meetings_section(pod, viewer, *, member_avail, viewer_zone, is_member, can_manage, now):
    """Context for the pod page Meetings section."""
    state = meeting_state(pod, now)
    rows = build_meeting_rows(
        pod, viewer, member_avail=member_avail, viewer_zone=viewer_zone,
        is_member=is_member, now=now, state=state,
    )
    empty = None
    if not rows:
        if len(member_avail) < 2:
            empty = ('Meetings start when someone joins', 'Once a second member joins, pick a suggested time together.')
        else:
            empty = ('No meetings yet', 'Pick a suggested time below to propose your first meeting.')
    return {
        'state': state,
        'rows': rows,
        'held': state.held,
        'total': pod.meeting_count,
        'all_done_line': MSG_ALL_DONE if state.held >= pod.meeting_count else '',
        'empty': empty,
        'call_link_url': pod.meeting_url,
        'call_link_label': call_link_label(pod.meeting_url),
        'can_manage': can_manage,
    }


# --- Pods tab and dashboard ------------------------------------------------------

def next_meeting_lines(pods, viewer_zone, now):
    """``{pod_id: line}`` for the viewer's own pods on the Pods tab."""
    pods = list(pods)
    if not pods:
        return {}
    by_pod = {}
    for meeting in PodMeeting.objects.filter(pod__in=pods).order_by('starts_at', 'pk'):
        by_pod.setdefault(meeting.pod_id, []).append(meeting)
    lines = {}
    for pod in pods:
        state = meeting_state(pod, now, meetings=by_pod.get(pod.pk, []))
        upcoming = [
            m for m in state.meetings
            if m.status == MEETING_STATUS_SCHEDULED and meeting_end(m) > now
        ]
        if upcoming:
            lines[pod.pk] = f'Next meeting: {format_in_zone(upcoming[0].starts_at, viewer_zone)}'
        elif state.has_open_proposal:
            lines[pod.pk] = 'Time proposed - waiting for confirmation'
    return lines


def dashboard_pod_meetings(user, now):
    """Pod meeting rows for ``user``'s ``Your week`` list (next 7 days).

    Only pods the member belongs to, in courses with pods switched on, not
    archived. Scheduled meetings the member said they can't make are left
    out. Live proposals (the head meeting of a weekly one) the member has
    not answered yet are listed as ``{pod} - Proposed time`` with the
    ``Needs your answer`` badge (issue #1934); once they answer, the row goes away.
    """
    slugs = pods_config.enabled_course_slugs()
    if not slugs or not getattr(user, 'is_authenticated', False):
        return []
    pods = list(
        Pod.objects.filter(memberships__user=user, cohort__course__slug__in=slugs)
        .exclude(status=POD_STATUS_ARCHIVED)
        .select_related('cohort__course')
        .distinct()
    )
    if not pods:
        return []
    meetings = list(PodMeeting.objects.filter(pod__in=pods).order_by('starts_at', 'pk'))
    answers = dict(
        PodMeetingResponse.objects.filter(
            user=user, meeting__in=[m.pk for m in meetings],
        ).values_list('meeting_id', 'response')
    )
    zone = zone_for(user)
    horizon = now + timedelta(days=DASHBOARD_DAYS)
    rows = []
    for pod in pods:
        state = meeting_state(pod, now, meetings=[m for m in meetings if m.pod_id == pod.pk])
        heads = {_proposal_head(state, m).pk for m in state.open_proposals}
        for meeting in state.meetings:
            if meeting_end(meeting) <= now or meeting.starts_at > horizon:
                continue
            if meeting.status == MEETING_STATUS_SCHEDULED and answers.get(meeting.pk) != MEETING_RESPONSE_CANT:
                title = f'{pod.name} - Meeting {state.numbers.get(meeting.pk)} of {pod.meeting_count}'
                url, badge = pod_url(pod), ''
            elif meeting.pk in heads and meeting.pk not in answers:
                title = f'{pod.name} - Proposed time'
                url, badge = f'{pod_url(pod)}#meetings', DASHBOARD_NEEDS_ANSWER
            else:
                continue
            rows.append({
                'title': title,
                'url': url,
                'starts_at': meeting.starts_at,
                'time_label': format_in_zone(meeting.starts_at, zone),
                'meeting_id': meeting.pk,
                'badge': badge,
            })
    rows.sort(key=lambda r: (r['starts_at'], r['meeting_id']))
    return rows


def _proposal_head(state, meeting):
    """The meeting that carries a proposal's answers (the series' first)."""
    if not meeting.series_id:
        return meeting
    return next(m for m in state.meetings if m.status == MEETING_STATUS_PROPOSED and m.series_id == meeting.series_id)
