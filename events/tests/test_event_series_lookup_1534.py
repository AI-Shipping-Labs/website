"""Event-series lookup ownership and typed-result contracts (issue #1534)."""

from django.test import TestCase

from events.models import EventSeries
from events.services.event_series_lookup import (
    EventSeriesLookupResult,
    EventSeriesLookupStatus,
    resolve_event_series,
)


class EventSeriesLookupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name="Community Sprint",
            slug="community-sprint",
        )
        cls.numeric_slug_series = EventSeries.objects.create(
            name="Numeric slug",
            slug="999999",
        )

    def test_resolves_integer_and_numeric_string_primary_keys(self):
        for raw in (self.series.pk, str(self.series.pk)):
            with self.subTest(raw=raw):
                result = resolve_event_series(raw)
                self.assertIsInstance(result, EventSeriesLookupResult)
                self.assertIs(result.status, EventSeriesLookupStatus.FOUND)
                self.assertEqual(result.event_series, self.series)

    def test_resolves_slug_and_falls_back_to_digit_only_slug(self):
        for raw, expected in (
            ("community-sprint", self.series),
            ("999999", self.numeric_slug_series),
        ):
            with self.subTest(raw=raw):
                result = resolve_event_series(raw)
                self.assertIs(result.status, EventSeriesLookupStatus.FOUND)
                self.assertEqual(result.event_series, expected)

    def test_distinguishes_blank_values(self):
        for raw in (None, ""):
            with self.subTest(raw=raw):
                result = resolve_event_series(raw)
                self.assertIs(result.status, EventSeriesLookupStatus.BLANK)
                self.assertIsNone(result.event_series)

    def test_distinguishes_invalid_types(self):
        for raw in (True, False, [], {}, 1.5):
            with self.subTest(raw=raw):
                result = resolve_event_series(raw)
                self.assertIs(result.status, EventSeriesLookupStatus.INVALID)
                self.assertIsNone(result.event_series)

    def test_distinguishes_unknown_id_and_slug(self):
        for raw in (987654, "missing-series"):
            with self.subTest(raw=raw):
                result = resolve_event_series(raw)
                self.assertIs(result.status, EventSeriesLookupStatus.NOT_FOUND)
                self.assertIsNone(result.event_series)


class EventSeriesLookupOwnershipTest(TestCase):
    def test_operator_views_import_the_canonical_resolver(self):
        from api.views import books as api_books
        from api.views import events as api_events
        from api.views import sprints as api_sprints
        from studio.views import books as studio_books
        from studio.views import sprints as studio_sprints

        modules = (
            api_books,
            api_events,
            api_sprints,
            studio_books,
            studio_sprints,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertIs(module.resolve_event_series, resolve_event_series)

        self.assertFalse(hasattr(studio_books, "_parse_event_series"))
        self.assertFalse(hasattr(studio_sprints, "_parse_event_series"))
        self.assertFalse(hasattr(api_events, "_resolve_event_series"))
