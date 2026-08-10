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


class BookRoadmapWeekGroupingTest(TestCase):
    """Chapters group into weeks on the member roadmap (week_number)."""

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone

        cls.book = Book.objects.create(
            title='Weekly Book', slug='weekly-book', author='Author',
            required_level=LEVEL_MAIN, status='current',
        )
        today = timezone.localdate()
        # Week 1 = chapters 0,1,2 (nearer deadline); week 2 = chapters 3,4.
        for n in (0, 1, 2):
            Chapter.objects.create(
                book=cls.book, number=n, title=f'Ch{n}', week_number=1,
                deadline=today + timedelta(days=7),
            )
        for n in (3, 4):
            Chapter.objects.create(
                book=cls.book, number=n, title=f'Ch{n}', week_number=2,
                deadline=today + timedelta(days=14),
            )
        cls.member = User.objects.create_user(email='wk@test.com', password='pw')
        cls.member.tier = Tier.objects.get(slug='main')
        cls.member.save()

    def test_roadmap_renders_a_group_per_week(self):
        self.client.force_login(self.member)
        response = self.client.get('/books/weekly-book')
        self.assertContains(response, 'data-week-number="1"')
        self.assertContains(response, 'data-week-number="2"')
        # Exactly two week groups, each with a heading.
        self.assertContains(response, 'book-week-heading', count=2)
        self.assertContains(response, '>Week 1<')
        self.assertContains(response, '>Week 2<')

    def test_week_label_overrides_the_default_heading(self):
        Chapter.objects.filter(book=self.book, number=0).update(
            week_label='Foundations',
        )
        self.client.force_login(self.member)
        response = self.client.get('/books/weekly-book')
        self.assertContains(response, '>Foundations<')
        self.assertContains(response, '>Week 2<')

    def test_this_week_lists_the_whole_week_set(self):
        self.client.force_login(self.member)
        response = self.client.get('/books/weekly-book')
        # Current chapter is in week 1, so the callout lists chapters 0,1,2.
        self.assertContains(response, 'book-this-week')
        self.assertContains(response, 'book-this-week-chapter', count=3)

    def test_book_without_week_numbers_renders_flat(self):
        flat = Book.objects.create(
            title='Flat Book', slug='flat-book', author='Author',
            required_level=LEVEL_MAIN, status='current',
        )
        Chapter.objects.create(book=flat, number=0, title='A')
        Chapter.objects.create(book=flat, number=1, title='B')
        self.client.force_login(self.member)
        response = self.client.get('/books/flat-book')
        self.assertContains(response, 'book-participation-body')
        self.assertContains(response, 'Ch. 0 — A')
        # No week headings when no chapter has a week_number.
        self.assertNotContains(response, 'book-week-heading')
