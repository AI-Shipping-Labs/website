"""Availability engine. Timezone-safe, duration-aware slot generation.

Ported from personal-scheduler.
Half-open intervals [start, end): a meeting ending at 11:00 does not overlap
one starting at 11:00. A candidate's whole protected interval [s-p, s+d+q)
must lie inside ONE continuous availability window and avoid busy intervals
plus local protection. Grid alignment happens in the schedule's timezone.
DST-safe via zoneinfo; durations are elapsed minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open overlap: [a_start, a_end) vs [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


def parse_hhmm(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def local_to_utc(naive_local: datetime, tz: ZoneInfo) -> datetime | None:
    """Local naive wall time -> UTC.

    None for nonexistent times (DST gap); ambiguous (repeated) times resolve to
    their first occurrence here — the grid stepper below additionally offers
    the second fold explicitly.
    """
    aware = naive_local.replace(tzinfo=tz)
    back = aware.astimezone(timezone.utc).astimezone(tz)
    if (back.hour, back.minute) != (naive_local.hour, naive_local.minute):
        folded = naive_local.replace(tzinfo=tz, fold=1)
        back_folded = folded.astimezone(timezone.utc).astimezone(tz)
        if (back_folded.hour, back_folded.minute) == (naive_local.hour, naive_local.minute):
            return folded.astimezone(timezone.utc)
        return None
    return aware.astimezone(timezone.utc)


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Berlin")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


@dataclass
class Policy:
    grid_min: int = 30
    notice_hours: float = 12.0
    horizon_days: int = 60
    pre_buffer_min: int = 0
    post_buffer_min: int = 0
    daily_count_limit: int | None = None
    daily_minutes_limit: int | None = None


@dataclass
class SlotOffer:
    start: datetime  # UTC
    end: datetime  # UTC


def weekly_windows_for_date(weekly: dict[int, list[tuple[str, str]]], weekday: int) -> list[tuple[str, str]]:
    """weekly is dict of weekday int (0=Mon..6=Sun) -> list of ("HH:MM", "HH:MM") tuples."""
    return list(weekly.get(weekday, []))


def open_windows_utc(
    target: date,
    schedule: dict,
    blocks: list[dict] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Continuous open windows (UTC) for one host-local date.

    A date override replaces that schedule's weekly hours for that date;
    a global full-day closure wins over everything, and interval blocks subtract.
    """
    tz = _tz(schedule.get("timezone", "Europe/Berlin"))
    weekly = schedule.get("weekly_windows", {}) or {}
    overrides = schedule.get("overrides", {}) or {}
    iso = target.isoformat()

    if iso in overrides:
        override = overrides[iso]
        if override.get("is_unavailable"):
            return []
        if override.get("start_time") and override.get("end_time"):
            day_windows = [(override["start_time"], override["end_time"])]
        else:
            day_windows = []
    else:
        day_windows = weekly_windows_for_date(weekly, target.weekday())

    blocks = blocks or []
    for block in blocks:
        if block.get("kind") == "fullday" and block.get("full_day_date") == iso:
            return []

    windows: list[tuple[datetime, datetime]] = []
    for window in day_windows:
        try:
            start_min, end_min = parse_hhmm(window[0]), parse_hhmm(window[1])
        except (IndexError, ValueError):
            continue
        if end_min <= start_min:
            continue
        start_utc = local_to_utc(datetime.combine(target, time(start_min // 60, start_min % 60)), tz)
        end_utc = local_to_utc(datetime.combine(target, time(end_min // 60, end_min % 60)), tz)
        if start_utc is None or end_utc is None or end_utc <= start_utc:
            continue
        windows.append((start_utc, end_utc))

    cuts: list[tuple[datetime, datetime]] = []
    for block in blocks:
        if block.get("kind") != "interval" or not block.get("start") or not block.get("end"):
            continue
        cuts.append((block["start"], block["end"]))

    if cuts:
        windows = _subtract(windows, cuts)
    windows.sort()
    return windows


def _subtract(
    windows: list[tuple[datetime, datetime]],
    cuts: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    out = []
    for start, end in windows:
        segments = [(start, end)]
        for cut_start, cut_end in cuts:
            remaining = []
            for seg_start, seg_end in segments:
                if not overlaps(seg_start, seg_end, cut_start, cut_end):
                    remaining.append((seg_start, seg_end))
                    continue
                if seg_start < cut_start:
                    remaining.append((seg_start, min(cut_start, seg_end)))
                if cut_end < seg_end:
                    remaining.append((max(cut_end, seg_start), seg_end))
            segments = remaining
        out.extend((s, e) for s, e in segments if e > s)
    return out


def subtract_own_event(
    busy: list[tuple[datetime, datetime]],
    own: tuple[datetime, datetime] | None,
) -> list[tuple[datetime, datetime]]:
    """Remove ONLY the booking's own event interval from busy data.

    A free/busy response may combine several overlapping events; subtracting
    the whole old interval would erase a sibling event's conflict.
    """
    if own is None:
        return list(busy)
    own_start, own_end = own
    out: list[tuple[datetime, datetime]] = []
    for busy_start, busy_end in busy:
        if not overlaps(busy_start, busy_end, own_start, own_end):
            out.append((busy_start, busy_end))
            continue
        if busy_start < own_start:
            out.append((busy_start, min(own_start, busy_end)))
        if own_end < busy_end:
            out.append((max(own_end, busy_start), busy_end))
    return [(s, e) for s, e in out if e > s]


def _grid_starts(
    window_start: datetime,
    window_end: datetime,
    grid_min: int,
    tz: ZoneInfo,
) -> list[datetime]:
    local_start = window_start.astimezone(tz).replace(second=0, microsecond=0)
    minutes = local_start.hour * 60 + local_start.minute
    first = local_start + timedelta(minutes=(-minutes) % grid_min)
    starts, current, guard = [], first.replace(tzinfo=None), 0
    while guard < 5000:
        guard += 1
        candidate = local_to_utc(current, tz)
        second = None
        folded = current.replace(tzinfo=tz, fold=1)
        if folded.utcoffset() != current.replace(tzinfo=tz, fold=0).utcoffset():
            back = folded.astimezone(timezone.utc).astimezone(tz)
            if (back.hour, back.minute) == (current.hour, current.minute):
                second = folded.astimezone(timezone.utc)
        if candidate is None:
            current += timedelta(minutes=grid_min)
            continue
        if candidate >= window_end and (second is None or second >= window_end):
            break
        if candidate >= window_start:
            starts.append(candidate)
        if second is not None and second != candidate and window_start <= second < window_end:
            starts.append(second)
        current += timedelta(minutes=grid_min)
    return sorted(starts)


def horizon_cutoff_utc(schedule_tz: str, horizon_days: int, now_utc: datetime) -> datetime:
    tz = _tz(schedule_tz)
    last_day = now_utc.astimezone(tz).date() + timedelta(days=horizon_days)
    cutoff = local_to_utc(datetime.combine(last_day + timedelta(days=1), time(0, 0)), tz)
    assert cutoff is not None
    return cutoff


def generate_slots(
    *,
    schedule: dict,
    policy: Policy,
    duration_min: int = 30,
    busy: list[tuple[datetime, datetime]] | None = None,
    local_protected: list[tuple[datetime, datetime]] | None = None,
    blocks: list[dict] | None = None,
    existing_bookings: list[dict] | None = None,
    now_utc: datetime | None = None,
    exclude_booking_id: str | int = "",
    own_event_interval: tuple[datetime, datetime] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    incomplete_busy: bool = False,
) -> dict:
    """Duration-aware slot offers.

    Raises RuntimeError on incomplete busy data:
    an uncertain range is unavailable for new confirmations, never all-free.
    """
    if incomplete_busy:
        raise RuntimeError("busy-time result incomplete; range unavailable")
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = _tz(schedule.get("timezone", "Europe/Berlin"))
    cutoff = horizon_cutoff_utc(schedule.get("timezone", "Europe/Berlin"), policy.horizon_days, now_utc)
    notice_at = now_utc + timedelta(hours=policy.notice_hours)
    blocks = blocks or []
    local_protected = local_protected or []
    busy = busy or []

    today = now_utc.astimezone(tz).date()
    last_allowed = today + timedelta(days=policy.horizon_days)
    start_day = max(from_date or today, today)
    end_day = min(to_date or last_allowed, last_allowed)

    effective_busy = subtract_own_event(busy, own_event_interval)

    usage: dict[str, dict] = {}
    for booking in (existing_bookings or []):
        if booking.get("status") not in ("confirmed", "pending_confirmation"):
            continue
        if exclude_booking_id and str(booking.get("id")) == str(exclude_booking_id):
            continue
        started = booking.get("start")
        if not started:
            continue
        if isinstance(started, str):
            started = datetime.fromisoformat(started)
        day = started.astimezone(tz).date().isoformat()
        entry = usage.setdefault(day, {"count": 0, "minutes": 0})
        entry["count"] += 1
        entry["minutes"] += int(booking.get("duration_min") or duration_min)

    slots: list[SlotOffer] = []
    day = start_day
    while day <= end_day:
        for window_start, window_end in open_windows_utc(day, schedule, blocks):
            for start in _grid_starts(window_start, window_end, policy.grid_min, tz):
                end = start + timedelta(minutes=duration_min)
                protected_start = start - timedelta(minutes=policy.pre_buffer_min)
                protected_end = end + timedelta(minutes=policy.post_buffer_min)
                if protected_start < window_start or protected_end > window_end:
                    continue
                if end > cutoff or start < notice_at:
                    continue
                if any(overlaps(protected_start, protected_end, b, e) for b, e in effective_busy):
                    continue
                if any(overlaps(protected_start, protected_end, b, e) for b, e in local_protected):
                    continue
                day_key = start.astimezone(tz).date().isoformat()
                used = usage.get(day_key, {"count": 0, "minutes": 0})
                if policy.daily_count_limit is not None and used["count"] + 1 > policy.daily_count_limit:
                    continue
                if policy.daily_minutes_limit is not None and used["minutes"] + duration_min > policy.daily_minutes_limit:
                    continue
                slots.append(SlotOffer(start=start, end=end))
        day += timedelta(days=1)

    slots.sort(key=lambda offer: offer.start)
    return {
        "slots": slots,
        "days": sorted({o.start.astimezone(tz).date().isoformat() for o in slots}),
        "cutoff": cutoff,
        "freshness": now_utc,
        "policy": policy,
    }


def expand_viewer_range(
    viewer_from: date,
    viewer_to: date,
    viewer_tz: str,
    host_tz: str,
) -> tuple[date, date]:
    """A viewer-local date query must include every intersecting host-local date."""
    viewer = _tz(viewer_tz)
    host = _tz(host_tz)
    start_utc = datetime.combine(viewer_from, time(0, 0)).replace(tzinfo=viewer).astimezone(timezone.utc)
    end_utc = datetime.combine(viewer_to + timedelta(days=1), time(0, 0)).replace(
        tzinfo=viewer
    ).astimezone(timezone.utc) - timedelta(seconds=1)
    return (
        start_utc.astimezone(host).date() - timedelta(days=1),
        end_utc.astimezone(host).date() + timedelta(days=1),
    )
