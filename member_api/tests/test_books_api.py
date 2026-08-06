"""Member Book Club reading API (issue #1364).

Exercises the ``books:read`` / ``books:write_progress`` scope surface: the
GET reading state, the PUT/DELETE per-chapter toggle, owner isolation between
two members' keys, scope enforcement, and the 403/404 access contract. Every
endpoint acts only on the key owner's own reading state.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import MemberAPIKey
from bookclub.models import Book, Chapter, ChapterRead
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


@tag("core")
class MemberBooksApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug="main")
        cls.free_tier = Tier.objects.get(slug="free")

        cls.book = Book.objects.create(
            title="Inference Engineering", slug="inference-engineering",
            author="Philip Kiely", required_level=LEVEL_MAIN,
            status="current", start_date=date(2026, 8, 10),
        )
        cls.chapters = [
            Chapter.objects.create(book=cls.book, number=i, title=f"Ch {i}")
            for i in range(5)
        ]
        cls.draft = Book.objects.create(
            title="Secret", slug="secret-draft", author="Nobody",
            required_level=LEVEL_MAIN, status="draft",
        )
        Chapter.objects.create(book=cls.draft, number=0, title="Hidden")

        cls.member = User.objects.create_user(email="owner@test.com")
        cls.member.tier = cls.main_tier
        cls.member.save()
        cls.other = User.objects.create_user(email="other@test.com")
        cls.other.tier = cls.main_tier
        cls.other.save()
        cls.free_user = User.objects.create_user(email="free@test.com")
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.key, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="agent",
        )
        cls.other_key, cls.other_plaintext = MemberAPIKey.create_for_user(
            user=cls.other, name="other agent",
        )
        cls.free_key, cls.free_plaintext = MemberAPIKey.create_for_user(
            user=cls.free_user, name="free agent",
        )
        # A read-only key (no ``books:write_progress``).
        cls.readonly_key, cls.readonly_plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="reader", scopes=["books:read"],
        )

    def _auth(self, plaintext):
        return {"HTTP_AUTHORIZATION": f"Token {plaintext}"}

    # ---- GET reading -----------------------------------------------------

    def test_get_reading_returns_per_chapter_state_and_totals(self):
        ChapterRead.objects.create(user=self.member, chapter=self.chapters[0])
        ChapterRead.objects.create(user=self.member, chapter=self.chapters[2])
        response = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["book"]["slug"], "inference-engineering")
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["done"], 2)
        self.assertEqual(data["pct"], 40)
        by_number = {c["number"]: c for c in data["chapters"]}
        self.assertTrue(by_number[0]["read"])
        self.assertIsNotNone(by_number[0]["read_at"])
        self.assertFalse(by_number[1]["read"])
        self.assertIsNone(by_number[1]["read_at"])

    def test_get_reading_requires_a_key(self):
        response = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "member_api_key_required")

    def test_get_reading_403_when_below_tier(self):
        response = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
            **self._auth(self.free_plaintext),
        )
        self.assertEqual(response.status_code, 403)

    def test_get_reading_404_for_draft_book(self):
        response = self.client.get(
            "/member-api/v1/books/secret-draft/reading",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 404)

    def test_get_reading_404_for_unknown_book(self):
        response = self.client.get(
            "/member-api/v1/books/does-not-exist/reading",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 404)

    # ---- PUT / DELETE chapter read --------------------------------------

    def test_put_marks_chapter_read_and_returns_totals(self):
        response = self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["read"])
        self.assertIsNotNone(data["read_at"])
        self.assertEqual(data["done"], 1)
        self.assertEqual(data["total"], 5)
        self.assertTrue(
            ChapterRead.objects.filter(
                user=self.member, chapter=self.chapters[0],
            ).exists(),
        )

    def test_put_is_idempotent(self):
        self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        second = self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["read"])
        self.assertEqual(
            ChapterRead.objects.filter(
                user=self.member, chapter=self.chapters[0],
            ).count(),
            1,
        )

    def test_delete_marks_chapter_unread_idempotently(self):
        ChapterRead.objects.create(user=self.member, chapter=self.chapters[0])
        first = self.client.delete(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["read"])
        self.assertEqual(first.json()["done"], 0)
        # Second delete is a no-op, still 200.
        second = self.client.delete(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["read"])

    def test_write_requires_write_progress_scope(self):
        # A ``books:read``-only key can GET but cannot mutate.
        get_ok = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
            **self._auth(self.readonly_plaintext),
        )
        self.assertEqual(get_ok.status_code, 200)
        put_denied = self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.readonly_plaintext),
        )
        self.assertEqual(put_denied.status_code, 401)
        self.assertEqual(
            put_denied.json()["code"], "invalid_member_api_key",
        )
        self.assertFalse(
            ChapterRead.objects.filter(user=self.member).exists(),
        )

    def test_put_403_when_below_tier(self):
        response = self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.free_plaintext),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ChapterRead.objects.exists())

    def test_put_404_for_unknown_chapter(self):
        response = self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/99/read",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "chapter_not_found")

    def test_put_404_for_draft_book(self):
        response = self.client.put(
            "/member-api/v1/books/secret-draft/chapters/0/read",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 404)

    # ---- Ownership isolation --------------------------------------------

    def test_two_members_have_independent_state_on_same_chapter(self):
        # Member A marks chapter 0 read via their key.
        self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        # Member B reads their own state — unaffected by A.
        b_reading = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
            **self._auth(self.other_plaintext),
        )
        self.assertEqual(b_reading.json()["done"], 0)
        b_by_number = {c["number"]: c for c in b_reading.json()["chapters"]}
        self.assertFalse(b_by_number[0]["read"])

        # B marks chapter 0 read; A's state stays a single owned row.
        self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.other_plaintext),
        )
        self.assertEqual(
            ChapterRead.objects.filter(chapter=self.chapters[0]).count(), 2,
        )
        a_reading = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
            **self._auth(self.plaintext),
        )
        self.assertEqual(a_reading.json()["done"], 1)

    def test_first_api_mark_flips_account_activated(self):
        self.assertFalse(self.member.account_activated)
        self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/0/read",
            **self._auth(self.plaintext),
        )
        self.member.refresh_from_db()
        self.assertTrue(self.member.account_activated)

    def test_malformed_authorization_header_is_401(self):
        response = self.client.get(
            "/member-api/v1/books/inference-engineering/reading",
            HTTP_AUTHORIZATION="Bearer nope",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "member_api_key_required")
