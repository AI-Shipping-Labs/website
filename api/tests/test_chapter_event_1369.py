"""Chapter-event admin API: attach/detach/reject matrix + serializer (#1369).

``POST/PATCH /api/books/<slug>/chapters[/<number>]`` accept an ``event``
property (pk/slug/null) enforcing the series-only rule, and echo a resolvable
``event`` object in the serialized chapter.
"""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN
from events.models import Event, EventSeries

User = get_user_model()


class ChapterEventApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-1369@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='t1369')

        cls.series = EventSeries.objects.create(
            name='Inference Engineering Book Club',
            slug='inference-engineering-book-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        start = timezone.now() + timedelta(days=7)
        cls.kickoff = Event.objects.create(
            title='Book club kickoff', slug='book-club-kickoff',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='upcoming', origin='studio', event_series=cls.series,
        )
        cls.unrelated = Event.objects.create(
            title='Unrelated Talk', slug='unrelated-talk',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='upcoming', origin='studio',
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
        Chapter.objects.create(book=cls.no_series_book, number=0, title='Ch 0')

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.staff_token.key}'}

    def _patch(self, path, payload):
        return self.client.patch(
            path, data=json.dumps(payload),
            content_type='application/json', **self._auth(),
        )

    def _post(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload),
            content_type='application/json', **self._auth(),
        )

    def test_attach_by_slug_returns_resolvable_event_object(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'event': 'book-club-kickoff'},
        )
        self.assertEqual(response.status_code, 200)
        event = response.json()['event']
        self.assertEqual(event['id'], self.kickoff.id)
        self.assertEqual(event['slug'], 'book-club-kickoff')
        self.assertEqual(event['title'], 'Book club kickoff')
        self.assertEqual(event['url'], self.kickoff.get_absolute_url())
        # Persisted + echoed on GET.
        get = self.client.get(
            '/api/books/inference-engineering/chapters/0', **self._auth(),
        )
        self.assertEqual(get.json()['event']['id'], self.kickoff.id)

    def test_attach_by_pk(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'event': self.kickoff.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['event']['id'], self.kickoff.id)

    def test_detach_with_null(self):
        self.chapter.event = self.kickoff
        self.chapter.save()
        response = self._patch(
            '/api/books/inference-engineering/chapters/0', {'event': None},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['event'])
        self.chapter.refresh_from_db()
        self.assertIsNone(self.chapter.event_id)

    def test_reject_event_outside_series_leaves_chapter_unchanged(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'event': 'unrelated-talk'},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn('event', response.json()['details'])
        self.chapter.refresh_from_db()
        self.assertIsNone(self.chapter.event_id)

    def test_reject_when_book_has_no_series(self):
        response = self._patch(
            '/api/books/no-series/chapters/0', {'event': self.kickoff.pk},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn('event', response.json()['details'])
        self.assertIn(
            'Link an event series', response.json()['details']['event'],
        )

    def test_reject_unknown_event_is_422_not_404(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0', {'event': 999999},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn('event', response.json()['details'])
        self.chapter.refresh_from_db()
        self.assertIsNone(self.chapter.event_id)

    def test_create_chapter_with_event(self):
        response = self._post(
            '/api/books/inference-engineering/chapters',
            {'number': 5, 'title': 'Production', 'event': 'book-club-kickoff'},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['event']['slug'], 'book-club-kickoff')

    def test_create_chapter_with_non_series_event_rejected(self):
        response = self._post(
            '/api/books/inference-engineering/chapters',
            {'number': 6, 'title': 'X', 'event': 'unrelated-talk'},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn('event', response.json()['details'])
        self.assertFalse(
            Chapter.objects.filter(book=self.book, number=6).exists(),
        )

    def test_serialize_chapter_event_null_when_unlinked(self):
        response = self.client.get(
            '/api/books/inference-engineering/chapters/0', **self._auth(),
        )
        self.assertIn('event', response.json())
        self.assertIsNone(response.json()['event'])
        # The bare event_id field is gone (migrated to the event object).
        self.assertNotIn('event_id', response.json())
