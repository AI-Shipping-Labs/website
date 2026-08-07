"""Admin API for Book Club summaries + notes-pull endpoint (issue #1368).

Covers the serializer summary fields, PATCH publish/unpublish on books and
chapters (idempotent + empty-publish 422 + non-admin 403), and the
``pull-notes`` automation-parity endpoint (read-only default + apply=true).
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import Token
from bookclub.models import Book, Chapter, Note
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


@tag("core")
class BookSummaryApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')
        cls.member_token = Token(
            key='non-staff-token', user=cls.member, name='legacy',
        )
        Token.objects.bulk_create([cls.member_token])
        cls.main_tier = Tier.objects.get(slug='main')
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
        )

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _patch(self, path, payload, *, token=None):
        return self.client.patch(
            path, data=json.dumps(payload),
            content_type='application/json', **self._auth(token),
        )

    def _post(self, path, payload=None, *, token=None):
        body = json.dumps(payload) if payload is not None else ''
        return self.client.post(
            path, data=body, content_type='application/json',
            **self._auth(token),
        )

    # ---- serializer fields ------------------------------------------------

    def test_serializer_exposes_summary_fields(self):
        response = self.client.get(
            '/api/books/inference-engineering', **self._auth(),
        )
        body = response.json()
        for key in ('summary', 'summary_published', 'summary_published_at'):
            self.assertIn(key, body)
        self.assertIn('summary', body['chapters'][0])
        self.assertIn('summary_published', body['chapters'][0])

    # ---- book PATCH -------------------------------------------------------

    def test_admin_publishes_book_summary(self):
        response = self._patch(
            '/api/books/inference-engineering',
            {'summary': 'Overall.', 'summary_published': True},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['summary'], 'Overall.')
        self.assertTrue(body['summary_published'])
        self.assertIsNotNone(body['summary_published_at'])

    def test_publish_is_idempotent(self):
        self._patch(
            '/api/books/inference-engineering',
            {'summary': 'Overall.', 'summary_published': True},
        )
        first = Book.objects.get(pk=self.book.pk).summary_published_at
        self._patch(
            '/api/books/inference-engineering',
            {'summary': 'Overall edited.', 'summary_published': True},
        )
        second = Book.objects.get(pk=self.book.pk).summary_published_at
        self.assertEqual(first, second)

    def test_empty_publish_returns_422(self):
        response = self._patch(
            '/api/books/inference-engineering',
            {'summary': '', 'summary_published': True},
        )
        self.assertEqual(response.status_code, 422)
        self.book.refresh_from_db()
        self.assertIsNone(self.book.summary_published_at)

    def test_non_admin_patch_forbidden(self):
        response = self._patch(
            '/api/books/inference-engineering',
            {'summary': 'x', 'summary_published': True},
            token=self.member_token,
        )
        self.assertIn(response.status_code, (401, 403))

    # ---- chapter PATCH ----------------------------------------------------

    def test_admin_publishes_chapter_summary(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'summary': 'Chapter body.', 'summary_published': True},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['summary_published'])
        get = self.client.get(
            '/api/books/inference-engineering/chapters/0', **self._auth(),
        )
        self.assertEqual(get.json()['summary'], 'Chapter body.')

    def test_chapter_empty_publish_422(self):
        response = self._patch(
            '/api/books/inference-engineering/chapters/0',
            {'summary_published': True},
        )
        self.assertEqual(response.status_code, 422)

    # ---- pull-notes endpoint ---------------------------------------------

    def _make_notes(self):
        for name in ['Ada', 'Lin']:
            user = User.objects.create_user(
                email=f'{name.lower()}@test.com', password='pw', first_name=name,
            )
            user.tier = self.main_tier
            user.save()
            Note.objects.create(
                chapter=self.chapter, user=user, body=f'{name} body.',
            )

    def test_pull_notes_default_is_read_only(self):
        self._make_notes()
        response = self._post(
            '/api/books/inference-engineering/chapters/0/summary/pull-notes',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['note_count'], 2)
        self.assertIn('- Ada: Ada body.', body['digest'])
        self.assertFalse(body['applied'])
        # Chapter draft unchanged.
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.summary, '')

    def test_pull_notes_apply_appends_to_draft(self):
        self._make_notes()
        response = self._post(
            '/api/books/inference-engineering/chapters/0/summary/pull-notes',
            {'apply': True},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('- Ada: Ada body.', body['summary'])
        self.assertEqual(body['notes_pulled'], 2)
        self.chapter.refresh_from_db()
        self.assertIn('- Lin: Lin body.', self.chapter.summary)
        # Never publishes.
        self.assertIsNone(self.chapter.summary_published_at)

    def test_pull_notes_non_admin_forbidden(self):
        response = self._post(
            '/api/books/inference-engineering/chapters/0/summary/pull-notes',
            token=self.member_token,
        )
        self.assertIn(response.status_code, (401, 403))

    def test_pull_notes_unknown_chapter_404(self):
        response = self._post(
            '/api/books/inference-engineering/chapters/99/summary/pull-notes',
        )
        self.assertEqual(response.status_code, 404)
