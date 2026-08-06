"""Server-rendered mark-read flow on /books/<slug> (issue #1364)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from bookclub.models import Book, Chapter, ChapterRead
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


@tag("core")
class ChapterReadViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title="Inference Engineering", slug="inference-engineering",
            author="Philip Kiely", required_level=LEVEL_MAIN,
            status="current", start_date=date(2026, 8, 10),
        )
        cls.chapters = [
            Chapter.objects.create(book=cls.book, number=i, title=f"Ch {i}")
            for i in range(5)
        ]
        cls.free_tier = Tier.objects.get(slug="free")
        cls.main_tier = Tier.objects.get(slug="main")

        cls.member = User.objects.create_user(email="main@test.com")
        cls.member.tier = cls.main_tier
        cls.member.save()

        cls.free_user = User.objects.create_user(email="free@test.com")
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

    @property
    def read_url_0(self):
        return "/books/inference-engineering/chapters/0/read"

    def test_mark_read_creates_row_and_redirects_to_book(self):
        self.client.force_login(self.member)
        response = self.client.post(self.read_url_0, {"read": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/books/inference-engineering", response["Location"])
        self.assertTrue(
            ChapterRead.objects.filter(
                user=self.member, chapter=self.chapters[0],
            ).exists(),
        )

    def test_mark_read_twice_is_idempotent(self):
        self.client.force_login(self.member)
        self.client.post(self.read_url_0, {"read": "1"})
        self.client.post(self.read_url_0, {"read": "1"})
        self.assertEqual(
            ChapterRead.objects.filter(
                user=self.member, chapter=self.chapters[0],
            ).count(),
            1,
        )

    def test_mark_unread_deletes_row(self):
        self.client.force_login(self.member)
        ChapterRead.objects.create(user=self.member, chapter=self.chapters[0])
        response = self.client.post(self.read_url_0, {"read": "0"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ChapterRead.objects.filter(
                user=self.member, chapter=self.chapters[0],
            ).exists(),
        )

    def test_progress_shows_on_book_detail_and_updates(self):
        self.client.force_login(self.member)
        response = self.client.get("/books/inference-engineering")
        self.assertContains(response, "0 of 5 read")
        self.client.post(self.read_url_0, {"read": "1"})
        response = self.client.get("/books/inference-engineering")
        self.assertContains(response, "1 of 5 read")
        # Chapter 0 now offers a "Mark unread" control.
        self.assertContains(response, "book-chapter-mark-unread")

    def test_anonymous_is_redirected_to_login_and_writes_no_row(self):
        response = self.client.post(self.read_url_0, {"read": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertEqual(ChapterRead.objects.count(), 0)

    def test_below_tier_member_gets_403_and_writes_no_row(self):
        self.client.force_login(self.free_user)
        response = self.client.post(self.read_url_0, {"read": "1"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ChapterRead.objects.count(), 0)

    def test_gated_member_sees_no_mark_control(self):
        self.client.force_login(self.free_user)
        response = self.client.get("/books/inference-engineering")
        self.assertNotContains(response, "book-chapter-mark-read")
        self.assertNotContains(response, "book-participation-body")
        self.assertContains(response, "book-guest-gate")

    def test_member_sees_mark_control(self):
        self.client.force_login(self.member)
        response = self.client.get("/books/inference-engineering")
        self.assertContains(response, "book-chapter-mark-read")
        self.assertContains(response, "book-reading-progress-bar")

    def test_draft_book_post_404s_for_non_staff(self):
        draft = Book.objects.create(
            title="Secret", slug="secret-draft", author="Nobody",
            required_level=LEVEL_MAIN, status="draft",
        )
        Chapter.objects.create(book=draft, number=0, title="Hidden")
        self.client.force_login(self.member)
        response = self.client.post(
            "/books/secret-draft/chapters/0/read", {"read": "1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_chapter_number_404s(self):
        self.client.force_login(self.member)
        response = self.client.post(
            "/books/inference-engineering/chapters/99/read", {"read": "1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_book_404s(self):
        self.client.force_login(self.member)
        response = self.client.post(
            "/books/does-not-exist/chapters/0/read", {"read": "1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.member)
        response = self.client.get(self.read_url_0)
        self.assertEqual(response.status_code, 405)

    def test_first_mark_read_flips_account_activated(self):
        self.assertFalse(self.member.account_activated)
        self.client.force_login(self.member)
        self.client.post(self.read_url_0, {"read": "1"})
        self.member.refresh_from_db()
        self.assertTrue(self.member.account_activated)
