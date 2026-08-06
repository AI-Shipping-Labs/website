"""Studio flows for series attach/detach + cadence-less collections (#1358).

- The event edit form exposes an editable Series selector that attaches/detaches
  an existing event without renumbering or renaming it.
- The event-series create form can create a ``No fixed cadence`` collection with
  no day/time and no generated occurrences.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from tests.fixtures import StaffUserMixin


class StudioEventSeriesAttachTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.series = EventSeries.objects.create(
            name="Book Club Collection",
            slug="book-club-collection",
            cadence="none",
            day_of_week=None,
            start_time=None,
            timezone="UTC",
        )
        # Dynamic future date so the fixture never rots into the past.
        self.start = (timezone.now() + timedelta(days=30)).replace(
            second=0, microsecond=0,
        )
        self.event = Event.objects.create(
            title="Standalone Event",
            slug="standalone-event",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            status="draft",
            timezone="UTC",
            origin="studio",
        )

    def _edit_payload(self, **overrides):
        data = {
            "title": self.event.title,
            "slug": self.event.slug,
            "event_date": self.start.strftime("%d/%m/%Y"),
            "event_time": self.start.strftime("%H:%M"),
            "duration_hours": "1",
            "timezone": "UTC",
            "status": self.event.status,
            "required_level": "0",
            "tags": "",
        }
        data.update(overrides)
        return data

    def test_edit_form_renders_series_selector(self):
        resp = self.client.get(f"/studio/events/{self.event.pk}/edit")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="studio-event-series-select"')
        self.assertContains(resp, "— None —")
        self.assertContains(resp, "Book Club Collection")

    def test_attach_via_form_sets_series_without_position_or_rename(self):
        resp = self.client.post(
            f"/studio/events/{self.event.pk}/edit",
            self._edit_payload(event_series=str(self.series.pk)),
        )
        self.assertEqual(resp.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.event_series_id, self.series.pk)
        self.assertIsNone(self.event.series_position)
        self.assertEqual(self.event.title, "Standalone Event")

    def test_detach_via_form_none_option(self):
        self.event.event_series = self.series
        self.event.save(update_fields=["event_series"])
        resp = self.client.post(
            f"/studio/events/{self.event.pk}/edit",
            self._edit_payload(event_series=""),
        )
        self.assertEqual(resp.status_code, 302)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.event_series_id)

    def test_attached_event_shows_part_of_series_line(self):
        self.event.event_series = self.series
        self.event.save(update_fields=["event_series"])
        resp = self.client.get(f"/studio/events/{self.event.pk}/edit")
        self.assertContains(resp, 'data-testid="event-parent-series"')
        self.assertContains(resp, "Book Club Collection")


class StudioEventSeriesCreateCollectionTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_create_form_renders_cadence_selector(self):
        resp = self.client.get("/studio/event-series/new")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="series-cadence-select"')
        self.assertContains(resp, "No fixed cadence")

    def test_create_collection_without_weekly_slot(self):
        resp = self.client.post("/studio/event-series/new", {
            "name": "Inference Book Club",
            "cadence": "none",
            "timezone": "UTC",
            "required_level": "0",
            "kind": "standard",
            "platform": "zoom",
            # Weekly-only fields intentionally left blank.
            "start_date": "",
            "start_time": "",
            "duration_hours": "",
            "occurrences": "",
        })
        self.assertEqual(resp.status_code, 302)
        series = EventSeries.objects.get(name="Inference Book Club")
        self.assertEqual(series.cadence, "none")
        self.assertIsNone(series.day_of_week)
        self.assertIsNone(series.start_time)
        self.assertEqual(series.events.count(), 0)

    def test_create_weekly_series_still_generates_occurrences(self):
        start_date = timezone.localdate() + timedelta(days=30)
        resp = self.client.post("/studio/event-series/new", {
            "name": "Weekly Series",
            "cadence": "weekly",
            "start_date": start_date.strftime("%d/%m/%Y"),
            "start_time": "17:00",
            "duration_hours": "1",
            "occurrences": "3",
            "timezone": "UTC",
            "required_level": "0",
            "kind": "standard",
            "platform": "zoom",
        })
        self.assertEqual(resp.status_code, 302)
        series = EventSeries.objects.get(name="Weekly Series")
        self.assertEqual(series.cadence, "weekly")
        # day_of_week is derived from the start date's weekday.
        self.assertEqual(series.day_of_week, start_date.weekday())
        self.assertEqual(series.events.count(), 3)
