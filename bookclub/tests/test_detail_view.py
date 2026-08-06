"""Public /books/<slug> detail gating (issue #1362)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


class BookDetailGatingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
            description='The technologies behind every AI product.',
        )
        Chapter.objects.create(book=cls.book, number=0, title='Inference')
        Chapter.objects.create(book=cls.book, number=1, title='Prerequisites')

        cls.free_tier = Tier.objects.get(slug='free')
        cls.main_tier = Tier.objects.get(slug='main')

        cls.free_user = User.objects.create_user(
            email='free@test.com', password='pw',
        )
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.main_user = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

    def test_header_and_gate_render_for_anonymous_guest(self):
        response = self.client.get('/books/inference-engineering')
        self.assertEqual(response.status_code, 200)
        # Header renders publicly.
        self.assertContains(response, 'Inference Engineering')
        self.assertContains(response, 'book-access-badge')
        # Participation body is replaced by exactly one gated card.
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'book-participation-body')

    def test_free_member_below_main_still_gated(self):
        self.client.force_login(self.free_user)
        response = self.client.get('/books/inference-engineering')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'book-participation-body')

    def test_main_member_sees_participation_body(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'book-participation-body')
        self.assertContains(response, 'Ch. 0 — Inference')
        self.assertNotContains(response, 'book-guest-gate')

    def test_upgrade_cta_targets_pricing(self):
        response = self.client.get('/books/inference-engineering')
        self.assertContains(response, '/pricing')

    def test_draft_book_404s_for_the_public(self):
        Book.objects.create(
            title='Secret Draft', slug='secret-draft', author='Nobody',
            required_level=LEVEL_MAIN, status='draft',
        )
        response = self.client.get('/books/secret-draft')
        self.assertEqual(response.status_code, 404)

    def test_draft_book_visible_to_staff(self):
        Book.objects.create(
            title='Secret Draft', slug='secret-draft', author='Nobody',
            required_level=LEVEL_MAIN, status='draft',
        )
        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get('/books/secret-draft')
        self.assertEqual(response.status_code, 200)

    def test_unknown_slug_404s(self):
        response = self.client.get('/books/does-not-exist')
        self.assertEqual(response.status_code, 404)
