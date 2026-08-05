from datetime import datetime, time, timezone as dt_tz

from events.models import Event
from events.models.event_series import EventSeries

series, _ = EventSeries.objects.get_or_create(
    slug="inference-engineering-book-club",
    defaults=dict(
        name="Inference Engineering Book Club",
        description="Book club events (collection). No fixed weekly cadence.",
        cadence="weekly",  # only sanctioned choice today; acts as a collection
        day_of_week=0,
        start_time=time(17, 0),
        timezone="Europe/Berlin",
        required_level=0,
        is_active=True,
    ),
)

ev, created = Event.objects.get_or_create(
    slug="inference-engineering-book-club-kickoff",
    defaults=dict(
        title="Inference Engineering Book Club — Kickoff",
        description="Kickoff session for the Inference Engineering book club.",
        kind="standard",
        platform="zoom",
        start_datetime=datetime(2026, 8, 10, 15, 0, tzinfo=dt_tz.utc),
        end_datetime=datetime(2026, 8, 10, 16, 0, tzinfo=dt_tz.utc),
        timezone="Europe/Berlin",
        required_level=0,
        status="upcoming",
        published=True,
        origin="studio",
        event_series=series,
    ),
)
# Ensure it's attached to the series even if it already existed.
if ev.event_series_id != series.id:
    ev.event_series = series
    ev.save(update_fields=["event_series"])

print("series", series.id, series.slug, "| event", ev.id, ev.slug,
      "created" if created else "existing", "| series_id", ev.event_series_id)
print("detail_url", ev.get_absolute_url())
