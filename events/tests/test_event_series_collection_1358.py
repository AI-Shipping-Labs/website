"""Cadence-less collection behavior for EventSeries (#1358).

A ``none``-cadence series has null ``day_of_week`` / ``start_time`` and must
never claim a weekly schedule. It should render (public + staff) with 0, 1, and
3 occurrences without raising.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries

User = get_user_model()


class CadenceLessCollectionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-coll-1358@test.com", password="pw", is_staff=True,
        )

    def _make_collection(self, slug, occurrence_count):
        series = EventSeries.objects.create(
            name="Book Club Collection",
            slug=slug,
            cadence="none",
            day_of_week=None,
            start_time=None,
            timezone="Europe/Berlin",
            required_level=0,
        )
        base = timezone.now() + timedelta(days=7)
        for i in range(occurrence_count):
            start = base + timedelta(days=7 * i)
            Event.objects.create(
                title=f"Session {i + 1}",
                slug=f"{slug}-session-{i + 1}",
                start_datetime=start,
                end_datetime=start + timedelta(hours=1),
                status="upcoming",
                published=True,
                origin="studio",
                event_series=series,
            )
        return series

    def test_none_series_is_never_regular_cadence(self):
        for count in (0, 1, 3):
            with self.subTest(count=count):
                series = self._make_collection(f"coll-reg-{count}", count)
                self.assertFalse(series.is_regular_cadence)

    def test_schedule_label_is_neutral_not_weekly(self):
        one = self._make_collection("coll-one", 1)
        self.assertIn("1 session", one.schedule_label)
        self.assertNotIn("Weekly", one.schedule_label)

        three = self._make_collection("coll-three", 3)
        self.assertIn("3 sessions", three.schedule_label)
        self.assertNotIn("Weekly", three.schedule_label)

    def test_zero_occurrence_schedule_label_is_blank_no_raise(self):
        empty = self._make_collection("coll-empty", 0)
        self.assertEqual(empty.schedule_label, "")

    def test_public_page_renders_for_one_and_three_occurrences(self):
        for count in (1, 3):
            with self.subTest(count=count):
                series = self._make_collection(f"coll-pub-{count}", count)
                resp = self.client.get(series.get_absolute_url())
                self.assertEqual(resp.status_code, 200)
                content = resp.content.decode()
                self.assertNotIn("Weekly on", content)

    def test_staff_preview_of_empty_collection_renders(self):
        series = self._make_collection("coll-staff-empty", 0)
        self.client.force_login(self.staff)
        resp = self.client.get(series.get_absolute_url())
        self.assertEqual(resp.status_code, 200)
