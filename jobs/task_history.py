"""Shared database filters for completed django-q task history."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

VALID_TASK_STATUSES = ("all", "success", "failed")


def is_valid_operator_date(raw):
    """Return whether a non-empty operator date is exactly ``YYYY-MM-DD``."""
    if not isinstance(raw, str) or not raw:
        return False
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%d") == raw


def parse_operator_date_range(date_from_raw, date_to_raw):
    """Return aware inclusive/exclusive boundaries and an optional error."""
    parsed = {}
    for field, raw in (("date_from", date_from_raw), ("date_to", date_to_raw)):
        if not raw:
            parsed[field] = None
            continue
        if not is_valid_operator_date(raw):
            return None, None, field
        parsed[field] = datetime.strptime(raw, "%Y-%m-%d").date()
    if parsed["date_from"] and parsed["date_to"] and parsed["date_from"] > parsed["date_to"]:
        return None, None, "date_from"
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(parsed["date_from"], time.min), tz) if parsed["date_from"] else None
    end = (
        timezone.make_aware(
            datetime.combine(parsed["date_to"] + timedelta(days=1), time.min),
            tz,
        )
        if parsed["date_to"]
        else None
    )
    return start, end, None


def filter_task_history(queryset, *, q="", status="all", start=None, end=None):
    q = (q or "").strip()
    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(func__icontains=q))
    if status == "success":
        queryset = queryset.filter(success=True)
    elif status == "failed":
        queryset = queryset.filter(success=False)
    if start is not None:
        queryset = queryset.filter(started__gte=start)
    if end is not None:
        queryset = queryset.filter(started__lt=end)
    return queryset
