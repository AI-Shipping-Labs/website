"""Canonical lookup of an event series from an operator-supplied value."""

from dataclasses import dataclass
from enum import StrEnum

from events.models import EventSeries


class EventSeriesLookupStatus(StrEnum):
    """Possible outcomes of resolving an event-series id or slug."""

    BLANK = "blank"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    FOUND = "found"


@dataclass(frozen=True)
class EventSeriesLookupResult:
    """A transport-independent event-series lookup result."""

    status: EventSeriesLookupStatus
    event_series: EventSeries | None = None


def resolve_event_series(raw: object) -> EventSeriesLookupResult:
    """Resolve an event series by pk or slug without choosing an HTTP response.

    ``None`` and the empty string are explicit unlink values. Integers and
    digit-like strings resolve by primary key first. A digit-only string that
    is not a primary key may still be a slug, matching the events API contract.
    """
    if raw is None or raw == "":
        return EventSeriesLookupResult(EventSeriesLookupStatus.BLANK)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return EventSeriesLookupResult(EventSeriesLookupStatus.INVALID)

    event_series = None
    if isinstance(raw, int):
        event_series = EventSeries.objects.filter(pk=raw).first()
    elif raw.lstrip("-").isdigit():
        event_series = EventSeries.objects.filter(pk=int(raw)).first()
        if event_series is None and raw.isdigit():
            event_series = EventSeries.objects.filter(slug=raw).first()
    else:
        event_series = EventSeries.objects.filter(slug=raw).first()

    if event_series is None:
        return EventSeriesLookupResult(EventSeriesLookupStatus.NOT_FOUND)
    return EventSeriesLookupResult(
        EventSeriesLookupStatus.FOUND,
        event_series=event_series,
    )
