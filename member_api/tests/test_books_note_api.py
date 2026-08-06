"""Member Book Club own-note API (issue #1365).

Exercises the owner-scoped ``PUT`` / ``DELETE`` (``books:write_notes``) and
``GET`` (``books:read``) surface for the caller's single per-chapter note,
plus the scope + tier + 404 access contract.
"""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import MemberAPIKey
from bookclub.models import Book, Chapter, Note
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()

NOTE_URL = "/member-api/v1/books/inference-engineering/chapters/0/note"


@tag("core")
class MemberBookNoteApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug="main")
        cls.free_tier = Tier.objects.get(slug="free")

        cls.book = Book.objects.create(
            title="Inference Engineering", slug="inference-engineering",
            author="Philip Kiely", required_level=LEVEL_MAIN,
            status="current", start_date=date(2026, 8, 10),
        )
        cls.chapter = Chapter.objects.create(book=cls.book, number=0, title="Inference")
        cls.draft = Book.objects.create(
            title="Secret", slug="secret-draft", author="Nobody",
            required_level=LEVEL_MAIN, status="draft",
        )
        Chapter.objects.create(book=cls.draft, number=0, title="Hidden")

        cls.member = User.objects.create_user(email="owner@test.com")
        cls.member.tier = cls.main_tier
        cls.member.save()
        cls.free_user = User.objects.create_user(email="free@test.com")
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        # A full-scope key and a read-only key for the same member.
        cls.key, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="agent",
        )
        cls.readonly_key, cls.readonly_plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="reader", scopes=["books:read"],
        )
        # A key that can track progress but must NOT publish notes.
        cls.progress_key, cls.progress_plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="progress bot",
            scopes=["books:read", "books:write_progress"],
        )
        cls.free_full_key, cls.free_full_plaintext = MemberAPIKey.create_for_user(
            user=cls.free_user, name="free agent",
        )

    def _auth(self, plaintext):
        return {"HTTP_AUTHORIZATION": f"Token {plaintext}"}

    def _put(self, plaintext, body):
        return self.client.put(
            NOTE_URL,
            data=json.dumps(body),
            content_type="application/json",
            **self._auth(plaintext),
        )

    # ---- PUT upsert ------------------------------------------------------

    def test_put_creates_then_updates_same_note(self):
        response = self._put(self.plaintext, {"body": "First"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["body"], "First")
        self.assertEqual(Note.objects.filter(user=self.member).count(), 1)

        response = self._put(self.plaintext, {"body": "Second", "diagram": "graph TD; A-->B"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["body"], "Second")
        self.assertEqual(data["diagram"], "graph TD; A-->B")
        self.assertEqual(Note.objects.filter(user=self.member).count(), 1)

    def test_put_first_save_marks_activated(self):
        self.assertFalse(self.member.account_activated)
        self._put(self.plaintext, {"body": "x"})
        self.member.refresh_from_db()
        self.assertTrue(self.member.account_activated)

    def test_put_empty_body_is_422(self):
        response = self._put(self.plaintext, {"body": "   "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(Note.objects.count(), 0)

    # ---- GET own note ----------------------------------------------------

    def test_get_returns_owner_note_with_read_key(self):
        Note.objects.create(chapter=self.chapter, user=self.member, body="Mine")
        response = self.client.get(NOTE_URL, **self._auth(self.readonly_plaintext))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["body"], "Mine")

    def test_get_404_when_no_note(self):
        response = self.client.get(NOTE_URL, **self._auth(self.plaintext))
        self.assertEqual(response.status_code, 404)

    # ---- DELETE ----------------------------------------------------------

    def test_delete_clears_note_idempotently(self):
        Note.objects.create(chapter=self.chapter, user=self.member, body="Mine")
        response = self.client.delete(NOTE_URL, **self._auth(self.plaintext))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Note.objects.filter(user=self.member).count(), 0)
        # Idempotent second delete.
        response = self.client.delete(NOTE_URL, **self._auth(self.plaintext))
        self.assertEqual(response.status_code, 200)

    # ---- Scope enforcement ----------------------------------------------

    def test_put_with_readonly_key_is_401(self):
        response = self._put(self.readonly_plaintext, {"body": "x"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "insufficient_scope")

    def test_put_with_progress_only_key_is_401(self):
        # A progress-tracking automation must not be able to publish notes.
        response = self._put(self.progress_plaintext, {"body": "x"})
        self.assertEqual(response.status_code, 401)

    def test_get_requires_a_key(self):
        response = self.client.get(NOTE_URL)
        self.assertEqual(response.status_code, 401)

    # ---- Tier + 404 gating ----------------------------------------------

    def test_put_below_tier_is_403(self):
        response = self._put(self.free_full_plaintext, {"body": "x"})
        self.assertEqual(response.status_code, 403)

    def test_put_draft_book_is_404(self):
        response = self.client.put(
            "/member-api/v1/books/secret-draft/chapters/0/note",
            data=json.dumps({"body": "x"}),
            content_type="application/json",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 404)

    def test_put_unknown_chapter_is_404(self):
        response = self.client.put(
            "/member-api/v1/books/inference-engineering/chapters/99/note",
            data=json.dumps({"body": "x"}),
            content_type="application/json",
            **self._auth(self.plaintext),
        )
        self.assertEqual(response.status_code, 404)

    def test_note_endpoint_in_openapi(self):
        from api.openapi import build_spec
        from member_api.urls import urlpatterns as member_urlpatterns

        document = build_spec(
            member_urlpatterns,
            title="AI Shipping Labs Member API",
            version="1.0.0",
            path_prefix="/member-api",
            docs_route_names={"member_api_openapi_json", "member_api_docs"},
        )
        self.assertIn(
            "/member-api/v1/books/{slug}/chapters/{number}/note",
            document["paths"],
        )
