"""Query-budget coverage for member event registration-state serialization."""

from datetime import UTC, datetime, timedelta

from django.db import connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time

from accounts.models import MemberAPIKey, User
from content.access import LEVEL_MAIN
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesOccurrenceOptOut,
    SeriesRegistration,
)
from tests.fixtures import TierSetupMixin

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
REGISTRATION_TABLES = (
    ("events_eventregistration", "eventregistration"),
    ("events_seriesregistration", "seriesregistration"),
    ("events_seriesoccurrenceoptout", "seriesoccurrenceoptout"),
)


def _strip_exists_subqueries(sql):
    """Return ``sql`` with ``EXISTS (...)`` subqueries removed."""
    pieces = []
    index = 0
    lower = sql.lower()
    while True:
        found = lower.find("exists", index)
        if found == -1:
            pieces.append(sql[index:])
            return "".join(pieces)
        pieces.append(sql[index:found])
        cursor = found + len("exists")
        while cursor < len(sql) and sql[cursor].isspace():
            cursor += 1
        if cursor >= len(sql) or sql[cursor] != "(":
            pieces.append(sql[found:cursor])
            index = cursor
            continue
        depth = 0
        while cursor < len(sql):
            char = sql[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            cursor += 1
        index = cursor


def _standalone_existence_count(captured, table_names):
    """Count outer ``LIMIT 1`` lookups against one of ``table_names``."""
    count = 0
    names = tuple(name.lower() for name in table_names)
    for query in captured.captured_queries:
        outer = " ".join(_strip_exists_subqueries(query["sql"]).split()).lower()
        from_table = any(
            f'from "{name}"' in outer or f"from {name} " in outer
            for name in names
        )
        if from_table and "limit 1" in outer:
            count += 1
    return count


@freeze_time(NOW)
@tag("core")
class MemberEventRegistrationQueryBudgetTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email="query-budget-member@test.com",
            tier=cls.main_tier,
        )
        cls.other = User.objects.create_user(
            email="query-budget-other@test.com",
            tier=cls.main_tier,
        )
        _, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member,
            name="events budget",
        )
        cls.series = EventSeries.objects.create(
            name="Office hours",
            slug="office-hours-budget",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.plaintext}"}

    def _session(self, index, **overrides):
        start = NOW + timedelta(days=1, hours=index)
        defaults = {
            "slug": f"budget-session-{index:02d}",
            "title": f"Budget session {index:02d}",
            "start_datetime": start,
            "end_datetime": start + timedelta(hours=1),
            "status": "upcoming",
            "published": True,
            "required_level": LEVEL_MAIN,
            "event_series": self.series,
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def _create_sessions(self, count, *, start_index=0):
        return [
            self._session(index)
            for index in range(start_index, start_index + count)
        ]

    def _mix_owner_state(self, events):
        event_registered = events[0]
        series_registered = events[1]
        opted_out = events[2]
        EventRegistration.objects.create(event=event_registered, user=self.member)
        SeriesRegistration.objects.create(series=self.series, user=self.member)
        SeriesOccurrenceOptOut.objects.create(
            series=self.series,
            event=opted_out,
            user=self.member,
        )
        EventRegistration.objects.create(event=series_registered, user=self.other)
        EventRegistration.objects.create(event=opted_out, user=self.other)
        SeriesRegistration.objects.create(series=self.series, user=self.other)
        SeriesOccurrenceOptOut.objects.create(
            series=self.series,
            event=series_registered,
            user=self.other,
        )
        expected = {}
        for event in events:
            if event.id == event_registered.id:
                expected[event.id] = {
                    "registration_source": "event",
                    "is_registered": True,
                    "registration_available": False,
                    "registration_targets": [],
                }
            elif event.id == opted_out.id:
                expected[event.id] = {
                    "registration_source": "none",
                    "is_registered": False,
                    "registration_available": True,
                    "registration_targets": ["series", "event"],
                }
            else:
                expected[event.id] = {
                    "registration_source": "series",
                    "is_registered": True,
                    "registration_available": False,
                    "registration_targets": [],
                }
        return expected

    def _assert_no_standalone_existence(self, captured):
        for table, alias in REGISTRATION_TABLES:
            with self.subTest(table=table):
                self.assertEqual(
                    _standalone_existence_count(captured, (table, alias)),
                    0,
                )

    def _assert_mixed_payload(self, items, expected):
        self.assertEqual(len(items), len(expected))
        for item in items:
            self.assertEqual(
                {
                    "registration_source": item["registration_source"],
                    "is_registered": item["is_registered"],
                    "registration_available": item["registration_available"],
                    "registration_targets": item["registration_targets"],
                },
                expected[item["id"]],
            )
            self.assertEqual(
                item["is_registered"],
                item["registration_source"] != "none",
            )

    def test_list_registration_state_is_constant_for_a_full_page(self):
        events = self._create_sessions(20)
        expected = self._mix_owner_state(events)

        with CaptureQueriesContext(connection) as captured:
            payload = self.client.get("/member-api/v1/events", **self._auth()).json()

        self.assertEqual(payload["pagination"]["page_size"], 20)
        self.assertEqual(payload["pagination"]["total"], 20)
        self._assert_mixed_payload(payload["events"], expected)
        self._assert_no_standalone_existence(captured)

        extra = self._create_sessions(20, start_index=20)
        EventRegistration.objects.create(event=extra[0], user=self.member)
        with CaptureQueriesContext(connection) as page_one:
            paged_payload = self.client.get(
                "/member-api/v1/events?page=1",
                **self._auth(),
            ).json()

        self.assertEqual(paged_payload["pagination"]["total"], 40)
        self.assertEqual(len(paged_payload["events"]), 20)
        self._assert_mixed_payload(paged_payload["events"], expected)
        self._assert_no_standalone_existence(page_one)

    def test_detail_uses_the_same_annotated_registration_state(self):
        events = self._create_sessions(3)
        expected = self._mix_owner_state(events)

        for event in events:
            with self.subTest(event=event.slug), CaptureQueriesContext(
                connection
            ) as captured:
                item = self.client.get(
                    f"/member-api/v1/events/{event.id}",
                    **self._auth(),
                ).json()
                self._assert_mixed_payload([item], {event.id: expected[event.id]})
                self._assert_no_standalone_existence(captured)
