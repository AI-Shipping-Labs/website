"""Series-only constraint for a chapter's linked event (issue #1369).

``Chapter.clean()`` is the single source of truth: a chapter's ``event`` must
be an occurrence of the book's own ``event_series``. These tests exercise the
model layer directly (the Studio view and admin API both call ``clean()``).
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN
from events.models import Event, EventSeries


class ChapterEventConstraintTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series_a = EventSeries.objects.create(
            name='Book Club', slug='book-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        cls.series_b = EventSeries.objects.create(
            name='Other Series', slug='other-series', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        start = timezone.now() + timedelta(days=7)
        cls.event_a = Event.objects.create(
            title='Kickoff', slug='kickoff', start_datetime=start,
            end_datetime=start + timedelta(hours=1), status='upcoming',
            origin='studio', event_series=cls.series_a,
        )
        cls.event_b = Event.objects.create(
            title='Unrelated Talk', slug='unrelated-talk', start_datetime=start,
            end_datetime=start + timedelta(hours=1), status='upcoming',
            origin='studio', event_series=cls.series_b,
        )
        cls.book_with_series = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
            event_series=cls.series_a,
        )
        cls.book_no_series = Book.objects.create(
            title='No Series Book', slug='no-series-book',
            author='Anon', required_level=LEVEL_MAIN, status='current',
        )

    def test_event_in_book_series_passes(self):
        chapter = Chapter(
            book=self.book_with_series, number=0, title='Ch 0',
            event=self.event_a,
        )
        chapter.full_clean()  # must not raise

    def test_event_outside_book_series_is_rejected(self):
        chapter = Chapter(
            book=self.book_with_series, number=0, title='Ch 0',
            event=self.event_b,
        )
        with self.assertRaises(ValidationError) as ctx:
            chapter.clean()
        self.assertIn('event', ctx.exception.message_dict)
        self.assertIn(
            "not part of the book's event series",
            ctx.exception.message_dict['event'][0],
        )

    def test_event_when_book_has_no_series_is_rejected(self):
        chapter = Chapter(
            book=self.book_no_series, number=0, title='Ch 0',
            event=self.event_a,
        )
        with self.assertRaises(ValidationError) as ctx:
            chapter.clean()
        self.assertIn('event', ctx.exception.message_dict)
        self.assertIn(
            'Link an event series to the book',
            ctx.exception.message_dict['event'][0],
        )

    def test_no_event_passes(self):
        chapter = Chapter(
            book=self.book_no_series, number=0, title='Ch 0',
        )
        chapter.full_clean()  # detach is always allowed
