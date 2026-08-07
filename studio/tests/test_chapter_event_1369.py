"""Studio chapter-event selector + series-only write path (issue #1369)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN
from events.models import Event, EventSeries

User = get_user_model()


class StudioChapterEventTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.series = EventSeries.objects.create(
            name='Book Club', slug='book-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        start = timezone.now() + timedelta(days=7)
        cls.kickoff = Event.objects.create(
            title='Kickoff', slug='kickoff', start_datetime=start,
            end_datetime=start + timedelta(hours=1), status='upcoming',
            origin='studio', event_series=cls.series,
        )
        cls.unrelated = Event.objects.create(
            title='Unrelated', slug='unrelated', start_datetime=start,
            end_datetime=start + timedelta(hours=1), status='upcoming',
            origin='studio',
        )
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
            event_series=cls.series,
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
        )
        cls.no_series_book = Book.objects.create(
            title='No Series', slug='no-series', author='Anon',
            required_level=LEVEL_MAIN, status='current',
        )
        cls.no_series_chapter = Chapter.objects.create(
            book=cls.no_series_book, number=0, title='Ch 0',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_selector_lists_only_series_occurrences(self):
        response = self.client.get(f'/studio/books/{self.book.pk}/')
        self.assertContains(response, 'book-chapter-event')
        self.assertContains(response, 'Kickoff')
        # Non-series events must not appear as options.
        self.assertNotContains(response, 'value="%d"' % self.unrelated.pk)

    def test_selector_disabled_and_hinted_without_series(self):
        response = self.client.get(f'/studio/books/{self.no_series_book.pk}/')
        self.assertContains(response, 'book-chapter-event-hint')
        self.assertContains(response, 'Link an event series to the book')
        self.assertContains(response, 'disabled')

    def test_attach_series_event_via_edit(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/edit',
            {'number': 0, 'title': 'Inference', 'event': self.kickoff.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.event_id, self.kickoff.id)

    def test_detach_via_none(self):
        self.chapter.event = self.kickoff
        self.chapter.save()
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/edit',
            {'number': 0, 'title': 'Inference', 'event': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertIsNone(self.chapter.event_id)

    def test_non_series_event_rejected_with_friendly_error(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/edit',
            {'number': 0, 'title': 'Inference', 'event': self.unrelated.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertIsNone(self.chapter.event_id)
        # The friendly error is surfaced on the next detail render.
        detail = self.client.get(f'/studio/books/{self.book.pk}/')
        # The apostrophe in "book's" is HTML-escaped in the rendered page.
        self.assertContains(detail, 'not part of the')
        self.assertContains(detail, 'event series')

    def test_create_chapter_with_event(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/add',
            {'number': 3, 'title': 'Hardware', 'event': self.kickoff.pk},
        )
        self.assertEqual(response.status_code, 302)
        created = Chapter.objects.get(book=self.book, number=3)
        self.assertEqual(created.event_id, self.kickoff.id)
