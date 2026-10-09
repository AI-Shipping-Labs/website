"""Suggested meeting times and timezone fit (issue #1918).

Deterministic, no randomness. Each member's weekly windows are local
wall-clock times in their IANA zone; they are expanded date by date into
concrete UTC intervals, so DST changes apply per member and per date (a
weekly window is never converted to UTC once). Work happens on a 15-minute
UTC grid so zones such as Asia/Kathmandu (GMT+05:45) line up.

Grid cell values: 0 = not free, 1 = free only in an ``If needed`` window,
2 = free in a ``Works well`` window.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from accounts.services.timezones import is_valid_timezone
from pods.models import PREFERENCE_PREFERRED, AvailabilityProfile
from pods.services import config as pods_config
from pods.services.people import city_from_zone, display_name, user_timezone_name

GRID_MINUTES = 15
GRID_STEP = timedelta(minutes=GRID_MINUTES)
SUGGESTION_LEAD = timedelta(hours=12)
FIT_HORIZON = timedelta(days=7)
SOCIAL_START_MINUTE = 8 * 60
SOCIAL_END_MINUTE = 22 * 60
MINUTES_PER_WEEK = 7 * 24 * 60
# Two suggestions closer than this on the weekly cycle are near-duplicates
# (``Tue 18:00`` and ``Tue 18:15`` of another week), issue #1927.
MIN_WEEKLY_GAP_MINUTES = 60

FIT_EVERYONE = 'everyone'
FIT_EVERYONE_IF_NEEDED = 'everyone_if_needed'
FIT_MISSING = 'missing'


@dataclass
class MemberAvailability:
    """One person's zone and weekly windows ``(weekday, start, end, pref)``."""

    user: object
    timezone_name: str
    windows: list = field(default_factory=list)

    @property
    def has_windows(self):
        return bool(self.windows) and bool(self.timezone_name)


@dataclass
class Slot:
    start: datetime
    end: datetime
    available: list
    if_needed: list
    missing: list
    local_starts: list
    considered: int
    # Pod members in total, with or without availability (issue #1927).
    member_total: int = 0

    @property
    def attending(self):
        return self.available + self.if_needed

    @property
    def fit(self):
        if self.missing:
            return FIT_MISSING
        return FIT_EVERYONE_IF_NEEDED if self.if_needed else FIT_EVERYONE

    @property
    def fit_label(self):
        """``Everyone`` only when every pod member has availability.

        When some members have not added availability, the label names how
        many were checked (``All 2 with availability - works well``) so a
        pod of 3 never reads ``Everyone`` on a 2-person fit.
        """
        if self.missing:
            names = ', '.join(display_name(m.user) for m in self.missing)
            return f'Missing {len(self.missing)}: {names}'
        who = 'Everyone'
        if self.member_total > self.considered:
            who = f'All {self.considered} with availability'
        if self.if_needed:
            return f'{who} - some if needed'
        return f'{who} - works well'

    @property
    def fit_tone(self):
        return {FIT_EVERYONE: 'success', FIT_EVERYONE_IF_NEEDED: 'info'}.get(self.fit, 'muted')


def load_member_availability(users):
    """``{user_id: MemberAvailability}`` for ``users`` in two queries."""
    users = list(users)
    profiles = {
        profile.user_id: profile
        for profile in AvailabilityProfile.objects.filter(
            user__in=[u.pk for u in users],
        ).prefetch_related('windows')
    }
    result = {}
    for user in users:
        profile = profiles.get(user.pk)
        user._pods_profile = profile
        tz_name = user_timezone_name(user, profile)
        windows = []
        if profile is not None and is_valid_timezone(profile.timezone):
            windows = [
                (w.weekday, w.start_minute, w.end_minute, w.preference)
                for w in profile.windows.all()
            ]
            tz_name = profile.timezone
        result[user.pk] = MemberAvailability(user=user, timezone_name=tz_name, windows=windows)
    return result


def _floor_to_grid(value):
    value = value.astimezone(UTC).replace(second=0, microsecond=0)
    return value - timedelta(minutes=value.minute % GRID_MINUTES)


class Grid:
    """A 15-minute UTC grid from ``start`` (floored) to ``end``."""

    def __init__(self, start, end):
        self.origin = _floor_to_grid(start)
        self.end = end.astimezone(UTC)
        total = (self.end - self.origin).total_seconds()
        self.size = max(int(total // (GRID_MINUTES * 60)), 0)
        self._memo = {}

    def time_at(self, index):
        return self.origin + GRID_STEP * index

    def index_at_or_after(self, value):
        delta = (value.astimezone(UTC) - self.origin).total_seconds()
        step = GRID_MINUTES * 60
        return max(int(-(-delta // step)), 0)

    def cells(self, member):
        """Expand ``member``'s weekly windows onto the grid (memoized)."""
        key = id(member)
        if key in self._memo:
            return self._memo[key]
        cells = [0] * self.size
        if member.has_windows and self.size:
            zone = ZoneInfo(member.timezone_name)
            first_day = self.origin.astimezone(zone).date() - timedelta(days=1)
            last_day = self.end.astimezone(zone).date() + timedelta(days=1)
            day = first_day
            by_weekday = {}
            for weekday, start, end, pref in member.windows:
                by_weekday.setdefault(weekday, []).append((start, end, pref))
            while day <= last_day:
                for start, end, pref in by_weekday.get(day.weekday(), ()):
                    midnight = datetime(day.year, day.month, day.day, tzinfo=zone)
                    local_start = midnight + timedelta(minutes=start)
                    local_end = (
                        datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(days=1)
                        if end >= 1440 else midnight + timedelta(minutes=end)
                    )
                    self._mark(cells, local_start, local_end, 2 if pref == PREFERENCE_PREFERRED else 1)
                day += timedelta(days=1)
        self._memo[key] = cells
        return cells

    def _mark(self, cells, local_start, local_end, value):
        utc_start = local_start.astimezone(UTC)
        utc_end = local_end.astimezone(UTC)
        step = GRID_MINUTES * 60
        first = int(-(-(utc_start - self.origin).total_seconds() // step))
        last = int((utc_end - self.origin).total_seconds() // step)
        for index in range(max(first, 0), min(last, self.size)):
            if cells[index] < value:
                cells[index] = value


def _status(cells, index, length):
    return min(cells[index:index + length]) if length else 0


def _local_minute(value, timezone_name):
    local = value.astimezone(ZoneInfo(timezone_name))
    return local.hour * 60 + local.minute


def minute_of_week(value):
    """UTC minutes since Monday 00:00 for ``value``."""
    value = value.astimezone(UTC)
    return value.weekday() * 1440 + value.hour * 60 + value.minute


def weekly_gap(first, second):
    """Cyclic distance in minutes between two minute-of-week values."""
    diff = abs(first - second) % MINUTES_PER_WEEK
    return min(diff, MINUTES_PER_WEEK - diff)


def required_attendance(considered):
    if considered < 2:
        return None
    if considered <= 2:
        return considered
    return max(2, considered - 1)


def suggest_slots(members, meeting_minutes, *, now=None, horizon_days=None, count=None, all_members=None):
    """Return up to ``count`` ranked :class:`Slot` rows for ``members``.

    ``members`` are the pod members' :class:`MemberAvailability`; only those
    with windows are considered. ``all_members`` (default ``members``) feed
    the per-member local-time strip, which also names members whose zone is
    known but who have no windows yet, and the member total behind the
    ``All N with availability`` label.

    Variety: never two slots on the same UTC date, and never two whose
    starts are less than ``MIN_WEEKLY_GAP_MINUTES`` apart on the weekly
    cycle (issue #1927).
    """
    now = now or timezone.now()
    horizon_days = horizon_days or pods_config.suggestion_horizon_days()
    count = count or pods_config.suggestion_count()
    considered = [m for m in members if m.has_windows]
    needed = required_attendance(len(considered))
    if needed is None:
        return []
    start = now + SUGGESTION_LEAD
    grid = Grid(start, now + timedelta(days=horizon_days))
    length = max(int(meeting_minutes) // GRID_MINUTES, 1)
    cells = {id(m): grid.cells(m) for m in considered}
    first_index = grid.index_at_or_after(start)
    candidates = []
    for index in range(first_index, grid.size - length + 1):
        statuses = [(m, _status(cells[id(m)], index, length)) for m in considered]
        attending = [(m, s) for m, s in statuses if s >= 1]
        if len(attending) < needed:
            continue
        slot_start = grid.time_at(index)
        if_needed_count = sum(1 for _m, s in attending if s == 1)
        unsocial = 0
        for member, _s in attending:
            local_start = _local_minute(slot_start, member.timezone_name)
            if local_start < SOCIAL_START_MINUTE or local_start + int(meeting_minutes) > SOCIAL_END_MINUTE:
                unsocial += 1
        rank = (-len(attending), if_needed_count, unsocial, slot_start)
        candidates.append((rank, index, statuses))
    candidates.sort(key=lambda row: row[0])

    chosen = []
    used_dates = set()
    used_weekly = []
    everyone = list(all_members or members)
    strip_members = [m for m in everyone if m.timezone_name]
    for _rank, index, statuses in candidates:
        slot_start = grid.time_at(index)
        weekly = minute_of_week(slot_start)
        if slot_start.date() in used_dates:
            continue
        if any(weekly_gap(weekly, other) < MIN_WEEKLY_GAP_MINUTES for other in used_weekly):
            continue
        used_dates.add(slot_start.date())
        used_weekly.append(weekly)
        chosen.append(Slot(
            start=slot_start,
            end=slot_start + timedelta(minutes=int(meeting_minutes)),
            available=[m for m, s in statuses if s == 2],
            if_needed=[m for m, s in statuses if s == 1],
            missing=[m for m, s in statuses if s == 0],
            local_starts=[
                {
                    'member': m,
                    'name': display_name(m.user),
                    'time': slot_start.astimezone(ZoneInfo(m.timezone_name)).strftime('%H:%M'),
                    'city': city_from_zone(m.timezone_name),
                    'timezone': m.timezone_name,
                }
                for m in strip_members
            ],
            considered=len(considered),
            member_total=max(len(everyone), len(considered)),
        ))
        if len(chosen) >= count:
            break
    return chosen


def member_strip(slot, members):
    """Per-slot strip for the member pod page: every pod member, in order.

    Members with availability get ``Name HH:MM City``; members without it
    (no windows, or no valid timezone) get ``no_availability`` so the page
    reads ``Mike K. - no availability`` instead of a time they never
    offered (issue #1927).
    """
    rows = []
    for member in members:
        row = {'member': member, 'name': display_name(member.user), 'no_availability': not member.has_windows}
        if member.has_windows:
            row.update({
                'time': slot.start.astimezone(ZoneInfo(member.timezone_name)).strftime('%H:%M'),
                'city': city_from_zone(member.timezone_name),
                'timezone': member.timezone_name,
            })
        rows.append(row)
    return rows


def slots_attendable(member, slots):
    """How many of ``slots`` ``member`` could attend (any window level)."""
    if member is None or not member.has_windows or not slots:
        return 0
    grid = Grid(min(s.start for s in slots), max(s.end for s in slots))
    cells = grid.cells(member)
    total = 0
    for slot in slots:
        index = grid.index_at_or_after(slot.start)
        length = int((slot.end - slot.start).total_seconds() // (GRID_MINUTES * 60))
        if index + length <= grid.size and _status(cells, index, length) >= 1:
            total += 1
    return total


def weekly_overlap_hours(viewer, others, *, now=None):
    """Whole hours in the next 7 days where ``viewer`` and every member of
    ``others`` with availability are free. ``None`` when it cannot be
    computed (viewer or the pod has no availability)."""
    if viewer is None or not viewer.has_windows:
        return None
    others = [m for m in others if m.has_windows]
    if not others:
        return None
    now = now or timezone.now()
    grid = Grid(now, now + FIT_HORIZON)
    rows = [grid.cells(viewer)] + [grid.cells(m) for m in others]
    free_cells = sum(1 for values in zip(*rows, strict=True) if min(values) >= 1)
    return (free_cells * GRID_MINUTES) // 60
