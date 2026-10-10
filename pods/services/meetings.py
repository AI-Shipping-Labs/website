"""Pod meeting rules shared by member views, Studio and the staff API (issue #1919).

A member proposes a time; the meeting is agreed (``scheduled``) once the
members who can make it reach the pod's required attendance (2 in a pod of
2 or 3, n - 1 in a pod of 4 or more). Staff, Studio and API meetings are
scheduled directly. Any member or staff can move, cancel or record the
outcome of a meeting; no re-agreement after a move.

Timezones: a meeting is a UTC instant plus the IANA zone it was proposed or
last moved in. Weekly repeats keep the same local wall-clock time in that
zone and are converted per date with ``zoneinfo``, never as ``+7 days`` in
UTC. A local time that does not exist (spring-forward gap) is shifted
forward by the gap; an ambiguous local time (fall-back) uses its first
occurrence. Both follow from attaching the zone with ``fold=0`` (PEP 495).
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from accounts.services.timezones import format_user_datetime, is_valid_timezone
from notifications.models import Notification
from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_RESPONSE_GOING,
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    MEETING_STATUS_PROPOSED,
    MEETING_STATUS_SCHEDULED,
    POD_SOURCE_MEMBER,
    POD_STATUS_ARCHIVED,
    AvailabilityProfile,
    Pod,
    PodMeeting,
    PodMeetingResponse,
)
from pods.services.meeting_rules import meeting_end, meeting_state
from pods.services.membership import PodError, pod_url
from pods.services.people import display_name, user_timezone_name
from pods.services.suggestions import required_attendance

NOTIFICATION_POD_MEETING = 'pod_meeting'

MIN_LEAD = timedelta(hours=1)
MAX_AHEAD = timedelta(days=180)
JOIN_WINDOW = timedelta(minutes=10)
GRID_MINUTES = 15
# Weekly repeats skip weeks that overlap another meeting; stop looking after
# two years so a pathological calendar cannot loop forever.
SERIES_SEARCH_WEEKS = 104
MEETING_DATETIME_FORMAT = '%a %b %d, %H:%M'
CALL_LINK_MAX_LENGTH = 500

CODE_LIMIT = 'meeting_limit_reached'
CODE_NOT_MOVABLE = 'meeting_not_movable'
CODE_PROPOSAL_EXISTS = 'proposal_exists'

OUTCOME_HELD = 'held'
OUTCOME_NOT_HELD = 'not_held'

MSG_PICK_TIME = 'Pick a date and time.'
MSG_TIME_RANGE = 'Pick a time at least 1 hour from now and within the next 6 months.'
MSG_QUARTER = 'Pick a start time on the quarter hour.'
MSG_PROPOSAL_EXISTS = 'There is already a proposed time. Confirm it or cancel it first.'
MSG_LOCKED = 'This meeting can no longer be changed.'
MSG_HAPPENED = 'This meeting has already happened.'
MSG_NOT_STARTED = 'This meeting has not started yet.'
MSG_NEEDS_TWO = 'Meetings start when someone joins.'
MSG_ARCHIVED = 'This pod is archived.'
MSG_MEMBERS_ONLY = 'Only pod members can do this.'
MSG_BAD_ZONE = 'Choose a valid timezone.'
MSG_BAD_RESPONSE = 'Choose whether you can make it.'
MSG_BAD_OUTCOME = 'Choose whether the meeting happened.'
MSG_BAD_STATUS = 'Status must be scheduled, held or cancelled.'
MSG_COUNT_NEEDS_REPEAT = 'Use repeat_weekly to create more than one meeting.'
MSG_HELD_NEEDS_AGREEMENT = 'Confirm the proposed time before marking it as held.'
MSG_CALL_LINK = 'Paste an https link to your call, for example https://meet.google.com/abc-defg-hij.'


def msg_limit(meeting_count):
    return (
        f'All {meeting_count} meetings are planned. '
        'Cancel one or raise the number of meetings to plan another.'
    )


# --- Zones and times ---------------------------------------------------------

def _profile(user):
    if not hasattr(user, '_pods_profile'):
        user._pods_profile = AvailabilityProfile.objects.filter(user_id=user.pk).first()
    return user._pods_profile


def zone_for(user):
    """The member's pod zone: availability zone, else preferred, else UTC."""
    if user is None:
        return 'UTC'
    return user_timezone_name(user, _profile(user)) or 'UTC'


def zone_viewer(zone_name):
    """A stand-in user for ``format_user_datetime`` in an explicit zone."""
    return SimpleNamespace(preferred_timezone=zone_name)


def format_in_zone(value, zone_name, fmt=MEETING_DATETIME_FORMAT):
    """``Tue Oct 14, 18:00 Europe/Berlin`` (zone token always appended)."""
    return format_user_datetime(value, zone_viewer(zone_name), fmt=fmt)


def local_to_utc(naive_local, zone_name):
    """Local wall-clock time in ``zone_name`` -> UTC instant.

    ``fold=0`` maps a nonexistent time with the offset before the
    transition, which shifts it forward by the gap (Berlin ``02:30`` on the
    spring-forward day becomes ``03:30``), and picks the first occurrence
    of an ambiguous fall-back time.
    """
    return naive_local.replace(tzinfo=ZoneInfo(zone_name), fold=0).astimezone(UTC)


def weekly_start(anchor, zone_name, weeks):
    """``anchor``'s local wall-clock time in ``zone_name``, ``weeks`` later."""
    local = anchor.astimezone(ZoneInfo(zone_name)).replace(tzinfo=None)
    return local_to_utc(local + timedelta(days=7 * weeks), zone_name)


def valid_zone(zone_name):
    zone_name = (zone_name or '').strip()
    if not is_valid_timezone(zone_name):
        raise PodError(MSG_BAD_ZONE, field='timezone')
    return zone_name


def parse_instant(raw, *, field='start'):
    """An ISO 8601 instant with an offset -> aware UTC datetime."""
    try:
        value = datetime.fromisoformat(str(raw or '').strip())
    except ValueError:
        raise PodError(MSG_PICK_TIME, field=field) from None
    if value.tzinfo is None:
        raise PodError(MSG_PICK_TIME, field=field)
    return value.astimezone(UTC)


def parse_local(date_raw, time_raw, zone_name):
    """Custom date and time typed in ``zone_name`` -> UTC instant."""
    try:
        day = date.fromisoformat(str(date_raw or '').strip())
        clock = time.fromisoformat(str(time_raw or '').strip())
    except ValueError:
        raise PodError(MSG_PICK_TIME, field='date') from None
    if clock.minute % GRID_MINUTES or clock.second or clock.microsecond or clock.tzinfo is not None:
        raise PodError(MSG_QUARTER, field='time')
    return local_to_utc(datetime.combine(day, clock), zone_name)


def validate_start(start, now):
    if start < now + MIN_LEAD or start > now + MAX_AHEAD:
        raise PodError(MSG_TIME_RANGE, field='start')
    if start.minute % GRID_MINUTES or start.second or start.microsecond:
        raise PodError(MSG_QUARTER, field='start')


# --- Call link -----------------------------------------------------------------

def clean_call_link(raw):
    """``''`` clears; otherwise an https URL of at most 500 characters."""
    value = str(raw or '').strip()
    if not value:
        return ''
    if len(value) > CALL_LINK_MAX_LENGTH:
        raise ValueError(MSG_CALL_LINK)
    try:
        URLValidator(schemes=['https'])(value)
    except ValidationError:
        raise ValueError(MSG_CALL_LINK) from None
    return value


def call_link_label(url):
    """``https://meet.google.com/abc-defg-hij`` -> ``meet.google.com/abc-defg-hij``."""
    if not url:
        return ''
    parts = urlsplit(url)
    return f'{parts.netloc}{parts.path}'.rstrip('/')


def set_call_link(pod, actor, raw):
    """Any pod member or staff sets or clears the pod call link."""
    if not _may_manage_meetings(pod, actor):
        raise PodError(MSG_MEMBERS_ONLY, code='forbidden')
    try:
        value = clean_call_link(raw)
    except ValueError as exc:
        raise PodError(str(exc), field='meeting_url') from None
    pod.meeting_url = value
    pod.save(update_fields=['meeting_url', 'updated_at'])
    return value


# --- Helpers -------------------------------------------------------------------

def _lock_pod(pod):
    # ``of=('self',)``: ``Pod.cohort`` is nullable, so ``select_related``
    # is a LEFT OUTER JOIN and PostgreSQL refuses FOR UPDATE on it.
    return Pod.objects.select_for_update(of=('self',)).select_related('cohort__course').get(pk=pod.pk)


def members_with_zones(pod):
    """Pod members with their availability profile attached (two queries)."""
    users = [m.user for m in pod.memberships.select_related('user').order_by('joined_at', 'pk')]
    profiles = {p.user_id: p for p in AvailabilityProfile.objects.filter(user__in=[u.pk for u in users])}
    for user in users:
        user._pods_profile = profiles.get(user.pk)
    return users


def _is_member(members, user):
    return user is not None and any(u.pk == user.pk for u in members)


def _may_manage_meetings(pod, actor):
    """Members and staff may move, cancel and record meetings."""
    if not getattr(actor, 'is_authenticated', False):
        return False
    return bool(actor.is_staff) or pod.memberships.filter(user=actor).exists()


def _require_member_or_staff(members, actor):
    if not (_is_member(members, actor) or getattr(actor, 'is_staff', False)):
        raise PodError(MSG_MEMBERS_ONLY, code='forbidden')


def _overlapping(state, start, minutes, exclude_ids=()):
    end = start + timedelta(minutes=minutes)
    for meeting in state.meetings:
        if meeting.pk in exclude_ids or not state.is_live(meeting):
            continue
        if meeting.starts_at < end and start < meeting_end(meeting):
            return meeting
    return None


def msg_overlap(state, other, zone_name):
    number = state.numbers.get(other.pk)
    label = format_in_zone(other.starts_at, zone_name)
    return f'This overlaps meeting {number} ({label}). Pick another time.'


def _check_overlap(state, start, minutes, zone_name, exclude_ids=()):
    other = _overlapping(state, start, minutes, exclude_ids)
    if other is not None:
        raise PodError(msg_overlap(state, other, zone_name), field='start')


def plan_weekly_starts(state, first_start, zone_name, count, minutes):
    """``count`` weekly starts from ``first_start`` (included).

    A repeat that overlaps an existing meeting is skipped forward one more
    week; the series still stops at ``count`` meetings.
    """
    starts = [first_start]
    week = 1
    while len(starts) < count and week <= SERIES_SEARCH_WEEKS:
        candidate = weekly_start(first_start, zone_name, week)
        week += 1
        if _overlapping(state, candidate, minutes) is None:
            starts.append(candidate)
    return starts


def proposal_meetings(meeting):
    """Every ``proposed`` meeting of ``meeting``'s proposal, in start order."""
    if meeting.status != MEETING_STATUS_PROPOSED:
        return [meeting]
    if not meeting.series_id:
        return [meeting]
    return list(
        PodMeeting.objects.filter(
            pod_id=meeting.pod_id, series_id=meeting.series_id, status=MEETING_STATUS_PROPOSED,
        ).order_by('starts_at', 'pk')
    )


def series_label(first, count, zone_name):
    """``Tuesdays at 18:00 Europe/Berlin, 4 meetings`` in ``zone_name``."""
    local = first.starts_at.astimezone(ZoneInfo(zone_name))
    return f'{local:%A}s at {local:%H:%M} {zone_name}, {count} meetings'


def _number_label(number):
    return f'meeting {number}' if number else 'a meeting'


# --- Notifications ---------------------------------------------------------------

def notify_members(pod, recipients, title_for, body=''):
    url = pod_url(pod)
    for user in recipients:
        Notification.objects.create(
            user=user,
            title=title_for(user)[:300],
            body=body,
            url=url,
            notification_type=NOTIFICATION_POD_MEETING,
        )


def _others(members, actor):
    return [u for u in members if actor is None or u.pk != actor.pk]


def _notify_proposed(pod, meetings, actor, members):
    first = meetings[0]
    name = display_name(actor)

    def title(user):
        zone = zone_for(user)
        when = series_label(first, len(meetings), zone) if len(meetings) > 1 else format_in_zone(first.starts_at, zone)
        return f'{name} proposed a time for {pod.name}: {when}'

    notify_members(pod, _others(members, actor), title)


def _notify_agreed(pod, meetings, members):
    first = meetings[0]
    body = f'Weekly, {len(meetings)} meetings.' if len(meetings) > 1 else ''
    notify_members(pod, members, lambda user: f'{pod.name} meets {format_in_zone(first.starts_at, zone_for(user))}', body)


# --- Agreement -------------------------------------------------------------------

def _maybe_agree(pod, head, actor, members, now):
    """Flip the proposal to ``scheduled`` once enough members can make it."""
    needed = required_attendance(len(members))
    if needed is None:
        return False
    going = PodMeetingResponse.objects.filter(
        meeting=head, response=MEETING_RESPONSE_GOING, user__in=[u.pk for u in members],
    ).count()
    if going < needed:
        return False
    meetings = proposal_meetings(head)
    PodMeeting.objects.filter(pk__in=[m.pk for m in meetings]).update(
        status=MEETING_STATUS_SCHEDULED,
        status_changed_by=actor,
        status_changed_at=now,
        updated_at=now,
    )
    _notify_agreed(pod, meetings, members)
    return True


# --- Member actions ------------------------------------------------------------------

@transaction.atomic
def propose_meeting(pod, actor, start, *, repeat_weekly=False, zone_name=None, now=None):
    """A member proposes ``start`` (one meeting, or the weekly series).

    Returns the created meetings (``proposed``, sharing a ``series_id`` when
    more than one). The proposer is recorded as ``going``.
    """
    now = now or timezone.now()
    pod = _lock_pod(pod)
    members = members_with_zones(pod)
    if not _is_member(members, actor):
        raise PodError(MSG_MEMBERS_ONLY, code='forbidden')
    if pod.status == POD_STATUS_ARCHIVED:
        raise PodError(MSG_ARCHIVED, code='pod_archived')
    if len(members) < 2:
        raise PodError(MSG_NEEDS_TWO)
    zone_name = valid_zone(zone_name or zone_for(actor))
    validate_start(start, now)
    state = meeting_state(pod, now)
    if state.has_open_proposal:
        raise PodError(MSG_PROPOSAL_EXISTS, code=CODE_PROPOSAL_EXISTS)
    remaining = pod.meeting_count - state.used
    if remaining < 1:
        raise PodError(msg_limit(pod.meeting_count), code=CODE_LIMIT)
    _check_overlap(state, start, pod.meeting_minutes, zone_name)
    count = remaining if repeat_weekly else 1
    starts = plan_weekly_starts(state, start, zone_name, count, pod.meeting_minutes)
    series_id = uuid.uuid4() if len(starts) > 1 else None
    meetings = [
        PodMeeting.objects.create(
            pod=pod,
            starts_at=value,
            duration_minutes=pod.meeting_minutes,
            timezone=zone_name,
            status=MEETING_STATUS_PROPOSED,
            series_id=series_id,
            created_via=POD_SOURCE_MEMBER,
            proposed_by=actor,
        )
        for value in starts
    ]
    PodMeetingResponse.objects.create(meeting=meetings[0], user=actor, response=MEETING_RESPONSE_GOING)
    _notify_proposed(pod, meetings, actor, members)
    _maybe_agree(pod, meetings[0], actor, members, now)
    return meetings


def _reload(meeting):
    return PodMeeting.objects.get(pk=meeting.pk)


def _ensure_changeable(meeting, state, now):
    if meeting.status in (MEETING_STATUS_HELD, MEETING_STATUS_CANCELLED) or state.is_expired(meeting):
        raise PodError(MSG_LOCKED, code=CODE_NOT_MOVABLE)
    if meeting.starts_at <= now:
        raise PodError(MSG_HAPPENED, code=CODE_NOT_MOVABLE)


@transaction.atomic
def respond_to_meeting(meeting, actor, response, *, now=None):
    """``going`` or ``cant_make_it``. Returns ``True`` when it agreed a proposal."""
    now = now or timezone.now()
    pod = _lock_pod(meeting.pod)
    meeting = _reload(meeting)
    members = members_with_zones(pod)
    if not _is_member(members, actor):
        raise PodError(MSG_MEMBERS_ONLY, code='forbidden')
    if response not in (MEETING_RESPONSE_GOING, MEETING_RESPONSE_CANT):
        raise PodError(MSG_BAD_RESPONSE, field='response')
    state = meeting_state(pod, now)
    _ensure_changeable(meeting, state, now)
    target = proposal_meetings(meeting)[0]
    previous = PodMeetingResponse.objects.filter(meeting=target, user=actor).values_list('response', flat=True).first()
    PodMeetingResponse.objects.update_or_create(meeting=target, user=actor, defaults={'response': response})
    if response == MEETING_RESPONSE_CANT and previous != MEETING_RESPONSE_CANT:
        name = display_name(actor)
        label = _number_label(state.numbers.get(target.pk))
        notify_members(pod, _others(members, actor), lambda _user: f"{name} can't make {pod.name} {label}")
    if response == MEETING_RESPONSE_GOING and target.status == MEETING_STATUS_PROPOSED:
        return _maybe_agree(pod, target, actor, members, now)
    return False


@transaction.atomic
def move_meeting(meeting, actor, start, *, zone_name=None, move_later=False, now=None):
    """Move ``meeting`` (and, with ``move_later``, the later series meetings).

    A proposal moves as a whole (its weekly shape is kept) and its answers
    reset to the mover. A scheduled meeting stays ``scheduled``; its
    ``Can't make it`` answers are cleared. All-or-nothing on overlap.
    Returns the moved meetings.
    """
    now = now or timezone.now()
    pod = _lock_pod(meeting.pod)
    meeting = _reload(meeting)
    members = members_with_zones(pod)
    _require_member_or_staff(members, actor)
    state = meeting_state(pod, now)
    _ensure_changeable(meeting, state, now)
    validate_start(start, now)
    zone_name = valid_zone(zone_name or zone_for(actor))
    is_proposal = meeting.status == MEETING_STATUS_PROPOSED
    if is_proposal:
        group = proposal_meetings(meeting)
    elif move_later and meeting.series_id:
        group = [meeting] + [
            m for m in state.series(meeting.series_id)
            if m.pk != meeting.pk and state.is_live(m) and m.starts_at > meeting.starts_at
            and m.starts_at > now and m.status == MEETING_STATUS_SCHEDULED
        ]
    else:
        group = [meeting]
    exclude = {m.pk for m in group}
    new_starts = [start] + [weekly_start(start, zone_name, k) for k in range(1, len(group))]
    for item, value in zip(group, new_starts, strict=True):
        _check_overlap(state, value, item.duration_minutes, zone_name, exclude)
    number = state.numbers.get(meeting.pk)
    for item, value in zip(group, new_starts, strict=True):
        item.previous_starts_at = item.starts_at
        item.starts_at = value
        item.timezone = zone_name
        item.moved_by = actor
        item.moved_at = now
        item.reminder_sent_at = None
        item.save(update_fields=[
            'previous_starts_at', 'starts_at', 'timezone', 'moved_by', 'moved_at',
            'reminder_sent_at', 'updated_at',
        ])
    ids = [m.pk for m in group]
    if is_proposal:
        PodMeetingResponse.objects.filter(meeting_id__in=ids).exclude(user=actor).delete()
        if _is_member(members, actor):
            PodMeetingResponse.objects.update_or_create(
                meeting=group[0], user=actor, defaults={'response': MEETING_RESPONSE_GOING},
            )
    else:
        PodMeetingResponse.objects.filter(meeting_id__in=ids, response=MEETING_RESPONSE_CANT).delete()
    name = display_name(actor)
    suffix = ' and the later meetings' if len(group) > 1 else ''

    def title(user):
        return f'{name} moved {pod.name} {_number_label(number)} to {format_in_zone(start, zone_for(user))}{suffix}'

    notify_members(pod, _others(members, actor), title)
    return group


@transaction.atomic
def cancel_meeting(meeting, actor, *, now=None):
    """Cancel a future proposal (the whole proposal) or one scheduled meeting."""
    now = now or timezone.now()
    pod = _lock_pod(meeting.pod)
    meeting = _reload(meeting)
    members = members_with_zones(pod)
    _require_member_or_staff(members, actor)
    state = meeting_state(pod, now)
    _ensure_changeable(meeting, state, now)
    group = proposal_meetings(meeting)
    number = state.numbers.get(meeting.pk)
    PodMeeting.objects.filter(pk__in=[m.pk for m in group]).update(
        status=MEETING_STATUS_CANCELLED, status_changed_by=actor, status_changed_at=now, updated_at=now,
    )
    name = display_name(actor)
    notify_members(pod, _others(members, actor), lambda _user: f'{name} cancelled {pod.name} {_number_label(number)}')
    return group


@transaction.atomic
def record_outcome(meeting, actor, outcome, *, now=None):
    """After its start, a scheduled meeting is marked ``held`` or ``cancelled``."""
    now = now or timezone.now()
    pod = _lock_pod(meeting.pod)
    meeting = _reload(meeting)
    members = members_with_zones(pod)
    _require_member_or_staff(members, actor)
    if outcome not in (OUTCOME_HELD, OUTCOME_NOT_HELD):
        raise PodError(MSG_BAD_OUTCOME, field='outcome')
    if meeting.status != MEETING_STATUS_SCHEDULED:
        raise PodError(MSG_LOCKED, code=CODE_NOT_MOVABLE)
    if meeting.starts_at > now:
        raise PodError(MSG_NOT_STARTED)
    meeting.status = MEETING_STATUS_HELD if outcome == OUTCOME_HELD else MEETING_STATUS_CANCELLED
    meeting.status_changed_by = actor
    meeting.status_changed_at = now
    meeting.save(update_fields=['status', 'status_changed_by', 'status_changed_at', 'updated_at'])
    return meeting


# --- Staff actions (Studio and API) ------------------------------------------------

@transaction.atomic
def schedule_meetings(pod, actor, start, *, zone_name, repeat_weekly=False, count=None, created_via, now=None):
    """Staff schedule agreed meetings directly. Returns the created meetings.

    ``count`` defaults to 1, or to every remaining meeting with
    ``repeat_weekly``. Members get the agreed notification.
    """
    now = now or timezone.now()
    pod = _lock_pod(pod)
    if pod.status == POD_STATUS_ARCHIVED:
        raise PodError(MSG_ARCHIVED, code='pod_archived')
    zone_name = valid_zone(zone_name)
    validate_start(start, now)
    state = meeting_state(pod, now)
    remaining = pod.meeting_count - state.used
    if count is None:
        count = remaining if repeat_weekly else 1
    if count > 1 and not repeat_weekly:
        raise PodError(MSG_COUNT_NEEDS_REPEAT, field='count')
    if remaining < 1 or count > remaining:
        raise PodError(msg_limit(pod.meeting_count), code=CODE_LIMIT)
    if count < 1:
        raise PodError('count must be 1 or more.', field='count')
    _check_overlap(state, start, pod.meeting_minutes, zone_name)
    starts = plan_weekly_starts(state, start, zone_name, count, pod.meeting_minutes)
    series_id = uuid.uuid4() if len(starts) > 1 else None
    staff_user = actor if getattr(actor, 'is_authenticated', False) else None
    meetings = [
        PodMeeting.objects.create(
            pod=pod,
            starts_at=value,
            duration_minutes=pod.meeting_minutes,
            timezone=zone_name,
            status=MEETING_STATUS_SCHEDULED,
            series_id=series_id,
            created_via=created_via,
            proposed_by=staff_user,
            status_changed_by=staff_user,
            status_changed_at=now,
        )
        for value in starts
    ]
    _notify_agreed(pod, meetings, members_with_zones(pod))
    return meetings


@transaction.atomic
def set_meeting_status(meeting, actor, status, *, now=None):
    """Staff override: any of ``scheduled``, ``held``, ``cancelled``.

    Bringing a cancelled or expired meeting back to ``scheduled`` re-checks
    the meeting count and overlaps. Scheduling a live proposal agrees the
    whole proposal and notifies members; cancelling a proposal cancels the
    whole proposal. ``held`` applies to exactly one meeting and is refused
    for a proposal (schedule it first) and before the meeting's start
    (issue #1934), the same rule as the member ``Mark as held``.
    """
    now = now or timezone.now()
    pod = _lock_pod(meeting.pod)
    meeting = _reload(meeting)
    if status not in (MEETING_STATUS_SCHEDULED, MEETING_STATUS_HELD, MEETING_STATUS_CANCELLED):
        raise PodError(MSG_BAD_STATUS, field='status')
    if status == meeting.status:
        return [meeting]
    if status == MEETING_STATUS_HELD and meeting.status == MEETING_STATUS_PROPOSED:
        # Explicit rule: a proposal (live or expired) is never marked held on
        # its own; marking one meeting of a proposed series would strand the
        # series' answers on it. Schedule it first, then mark it held.
        raise PodError(MSG_HELD_NEEDS_AGREEMENT, field='status')
    if status == MEETING_STATUS_HELD and meeting.starts_at > now:
        raise PodError(MSG_NOT_STARTED, field='status')
    state = meeting_state(pod, now)
    group = proposal_meetings(meeting) if status != MEETING_STATUS_HELD else [meeting]
    if status == MEETING_STATUS_SCHEDULED:
        returning = [m for m in group if not state.is_live(m)]
        if state.used + len(returning) > pod.meeting_count:
            raise PodError(msg_limit(pod.meeting_count), code=CODE_LIMIT)
        exclude = {m.pk for m in group}
        for item in returning:
            _check_overlap(state, item.starts_at, item.duration_minutes, item.timezone, exclude)
    was_proposal = meeting.status == MEETING_STATUS_PROPOSED and state.is_live(meeting)
    PodMeeting.objects.filter(pk__in=[m.pk for m in group]).update(
        status=status, status_changed_by=actor if getattr(actor, 'is_authenticated', False) else None,
        status_changed_at=now, updated_at=now,
    )
    if status == MEETING_STATUS_SCHEDULED and was_proposal:
        _notify_agreed(pod, group, members_with_zones(pod))
    return group


__all__ = [
    'CODE_LIMIT',
    'CODE_NOT_MOVABLE',
    'JOIN_WINDOW',
    'MSG_CALL_LINK',
    'MSG_PROPOSAL_EXISTS',
    'NOTIFICATION_POD_MEETING',
    'OUTCOME_HELD',
    'OUTCOME_NOT_HELD',
    'call_link_label',
    'cancel_meeting',
    'clean_call_link',
    'format_in_zone',
    'local_to_utc',
    'move_meeting',
    'msg_limit',
    'parse_instant',
    'parse_local',
    'propose_meeting',
    'record_outcome',
    'respond_to_meeting',
    'schedule_meetings',
    'set_call_link',
    'set_meeting_status',
    'validate_start',
    'weekly_start',
    'zone_for',
]
