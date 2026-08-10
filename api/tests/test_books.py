"""Book Club admin API (issue #1362)."""

import json
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN
from events.models.event_series import EventSeries

User = get_user_model()


class BookApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')
        # A legacy non-staff token: created via bulk_create to bypass the
        # staff-only clean() guard, mirroring the sprint API auth-matrix tests.
        cls.member_token = Token(
            key='non-staff-book-token', user=cls.member, name='legacy-non-staff',
        )
        Token.objects.bulk_create([cls.member_token])

        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
            start_date=date(2026, 8, 10),
        )
        Chapter.objects.create(book=cls.book, number=0, title='Inference')

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _post(self, path, payload, *, token=None):
        return self.client.post(
            path, data=json.dumps(payload),
            content_type='application/json', **self._auth(token),
        )

    def _patch(self, path, payload, *, token=None):
        return self.client.patch(
            path, data=json.dumps(payload),
            content_type='application/json', **self._auth(token),
        )


class BooksCollectionTest(BookApiTestBase):
    def test_list_returns_books(self):
        response = self.client.get('/api/books', **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('books', body)
        slugs = {b['slug'] for b in body['books']}
        self.assertIn('inference-engineering', slugs)

    def test_admin_creates_book(self):
        response = self._post('/api/books', {
            'title': 'Designing ML Systems', 'author': 'Chip Huyen',
            'slug': 'designing-ml-systems', 'required_level': LEVEL_MAIN,
            'status': 'upcoming',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['slug'], 'designing-ml-systems')
        self.assertTrue(Book.objects.filter(slug='designing-ml-systems').exists())

    def test_non_admin_token_cannot_create(self):
        before = Book.objects.count()
        response = self._post('/api/books', {
            'title': 'X', 'author': 'Y', 'slug': 'x-book',
        }, token=self.member_token)
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(Book.objects.count(), before)

    def test_create_rejects_duplicate_slug(self):
        response = self._post('/api/books', {
            'title': 'Dup', 'author': 'Y', 'slug': 'inference-engineering',
        })
        self.assertEqual(response.status_code, 422)

    def test_create_accepts_event_series_by_pk_and_slug(self):
        series = EventSeries.objects.create(
            slug='ie-book-club', name='IE Book Club', cadence='weekly',
            day_of_week=0, start_time=time(17, 0), timezone='Europe/Berlin',
            required_level=0, is_active=True,
        )
        by_pk = self._post('/api/books', {
            'title': 'By Pk', 'author': 'A', 'slug': 'by-pk',
            'event_series': series.pk,
        })
        self.assertEqual(by_pk.status_code, 201)
        self.assertEqual(by_pk.json()['event_series']['slug'], 'ie-book-club')

        by_slug = self._post('/api/books', {
            'title': 'By Slug', 'author': 'A', 'slug': 'by-slug',
            'event_series': 'ie-book-club',
        })
        self.assertEqual(by_slug.status_code, 201)
        self.assertEqual(by_slug.json()['event_series']['id'], series.pk)

    def test_create_rejects_unknown_event_series(self):
        response = self._post('/api/books', {
            'title': 'Bad', 'author': 'A', 'slug': 'bad-series',
            'event_series': 'does-not-exist',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'unknown_series')


class BookDetailTest(BookApiTestBase):
    def test_get_book_includes_chapters(self):
        response = self.client.get(
            '/api/books/inference-engineering', **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['slug'], 'inference-engineering')
        self.assertEqual(len(body['chapters']), 1)

    def test_admin_patches_book_status(self):
        response = self._patch(
            '/api/books/inference-engineering', {'status': 'finished'},
        )
        self.assertEqual(response.status_code, 200)
        self.book.refresh_from_db()
        self.assertEqual(self.book.status, 'finished')

    def test_patch_book_required_level_registered(self):
        # "Free with sign-in" (LEVEL_REGISTERED=5) is now a valid book level.
        response = self._patch(
            '/api/books/inference-engineering', {'required_level': 5},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['required_level'], 5)
        self.book.refresh_from_db()
        self.assertEqual(self.book.required_level, 5)

    def test_non_admin_cannot_patch(self):
        response = self._patch(
            '/api/books/inference-engineering', {'status': 'finished'},
            token=self.member_token,
        )
        self.assertIn(response.status_code, (401, 403))

    def test_delete_returns_405_studio_pointer(self):
        response = self.client.delete(
            '/api/books/inference-engineering', **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()['code'], 'book_delete_not_available')

    def test_unknown_book_404(self):
        response = self.client.get('/api/books/nope', **self._auth())
        self.assertEqual(response.status_code, 404)


class BookChaptersApiTest(BookApiTestBase):
    def test_list_chapters_ordered(self):
        Chapter.objects.create(book=self.book, number=2, title='Two')
        Chapter.objects.create(book=self.book, number=1, title='One')
        response = self.client.get(
            '/api/books/inference-engineering/chapters', **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        numbers = [c['number'] for c in response.json()['chapters']]
        self.assertEqual(numbers, [0, 1, 2])

    def test_admin_creates_chapter(self):
        response = self._post(
            '/api/books/inference-engineering/chapters',
            {'number': 1, 'title': 'Prerequisites', 'week_label': 'Week 2'},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            Chapter.objects.filter(book=self.book, number=1).exists(),
        )

    def test_duplicate_chapter_number_rejected(self):
        response = self._post(
            '/api/books/inference-engineering/chapters',
            {'number': 0, 'title': 'Dup'},
        )
        self.assertEqual(response.status_code, 422)

    def test_non_admin_cannot_create_chapter(self):
        response = self._post(
            '/api/books/inference-engineering/chapters',
            {'number': 5, 'title': 'X'}, token=self.member_token,
        )
        self.assertIn(response.status_code, (401, 403))

    def test_patch_chapter_deadline(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'deadline': '2026-09-10'},
        )
        self.assertEqual(response.status_code, 200)
        chapter = Chapter.objects.get(book=self.book, number=0)
        self.assertEqual(chapter.deadline, date(2026, 9, 10))

    def test_patch_chapter_week_number(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'week_number': 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['week_number'], 2)
        chapter = Chapter.objects.get(book=self.book, number=0)
        self.assertEqual(chapter.week_number, 2)
        # Null clears the grouping.
        self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'week_number': None},
        )
        chapter.refresh_from_db()
        self.assertIsNone(chapter.week_number)

    def test_patch_chapter_week_number_rejects_zero(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'week_number': 0},
        )
        self.assertEqual(response.status_code, 422)

    def test_chapter_delete_returns_405(self):
        response = self.client.delete(
            '/api/books/inference-engineering/chapters/0', **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()['code'], 'chapter_delete_not_available')

    def test_unknown_chapter_404(self):
        response = self.client.get(
            '/api/books/inference-engineering/chapters/99', **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
