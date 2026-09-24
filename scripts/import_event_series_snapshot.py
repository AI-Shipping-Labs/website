"""Load a selected production event-series API snapshot into a local database.

The JSON data stays outside the repository (under ``~/prod``). This script
contains only the mapping and deliberately omits host, attendee, Zoom, and
private recording fields from the import. It writes only with the project's
``website.settings``, ``DEBUG=True``, and a SQLite database inside this
checkout; inherited remote database settings are refused.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Running a file by path puts ``scripts/`` at sys.path[0], not the project
# root. Add the root before importing Django so this works from any cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

configured_settings_module = os.environ.get("DJANGO_SETTINGS_MODULE")
if configured_settings_module not in (None, "website.settings"):
    raise SystemExit("refusing to run with non-local Django settings")
os.environ["DJANGO_SETTINGS_MODULE"] = "website.settings"

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils.dateparse import parse_datetime, parse_time  # noqa: E402

from content.models.cohort import Cohort  # noqa: E402
from events.models import Event, EventSeries  # noqa: E402

SERIES_FIELDS = (
    "name",
    "description",
    "cadence",
    "day_of_week",
    "timezone",
    "required_level",
    "is_active",
    "visibility",
)
EVENT_FIELDS = (
    "title",
    "description",
    "kind",
    "platform",
    "timezone",
    "location",
    "tags",
    "required_level",
    "status",
    "published",
    "series_position",
    "recording_url",
    "recap_notes",
    "materials",
    "timestamps",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--cohort-id", type=int, required=True)
    args = parser.parse_args()

    database = settings.DATABASES["default"]
    if not settings.DEBUG or database.get("ENGINE") != "django.db.backends.sqlite3":
        parser.error("refusing writes unless DEBUG=True and the default database is local SQLite")
    if settings.DATABASE_ROUTERS:
        parser.error("refusing writes when database routers are configured")
    database_name = database.get("NAME")
    if database_name != ":memory:":
        database_path = Path(database_name).expanduser().resolve()
        try:
            database_path.relative_to(PROJECT_ROOT)
        except ValueError:
            parser.error("refusing writes because the SQLite database is outside this checkout")

    snapshot = json.loads(args.snapshot.read_text())
    source_series = snapshot["series"]
    source_events = snapshot["events"]
    expected_slugs = {item["slug"] for item in source_series["occurrences"]}
    actual_slugs = {item["slug"] for item in source_events}
    if expected_slugs != actual_slugs:
        raise ValueError("Snapshot occurrences do not match event details")
    if not source_events:
        raise ValueError("Snapshot has no events")

    with transaction.atomic(using="default"):
        cohort = Cohort.objects.using("default").select_related("course").get(pk=args.cohort_id)
        if cohort.course.slug != "ai-buildcamp":
            raise ValueError("Refusing to link this snapshot to a non-Buildcamp cohort")

        series_defaults = {name: source_series[name] for name in SERIES_FIELDS}
        series_defaults["start_time"] = parse_time(source_series["start_time"])
        series, _ = EventSeries.objects.using("default").update_or_create(
            slug=source_series["slug"],
            defaults=series_defaults,
        )

        for source_event in source_events:
            event_defaults = {name: source_event[name] for name in EVENT_FIELDS}
            event_defaults.update(
                {
                    "start_datetime": parse_datetime(source_event["start_datetime"]),
                    "end_datetime": parse_datetime(source_event["end_datetime"]),
                    "event_series": series,
                }
            )
            Event.objects.using("default").update_or_create(
                slug=source_event["slug"],
                defaults=event_defaults,
            )

        cohort.event_series = series
        cohort.save(using="default", update_fields=["event_series"])

    print(f"Imported series {series.pk} with {len(source_events)} events; linked cohort {cohort.pk}.")


if __name__ == "__main__":
    main()
