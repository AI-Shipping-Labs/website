"""Member Book Club reading-profile visibility API (issue #1366).

Exercises the owner-scoped, book-agnostic ``GET`` (``books:read``) / ``PUT``
(``books:write_profile``) surface for the caller's reading-profile visibility,
plus scope enforcement, bad-value rejection, and OpenAPI registration.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import MemberAPIKey
from bookclub.models import READER_VISIBILITY_PUBLIC, ReaderProfile
from payments.models import Tier

User = get_user_model()

PROFILE_URL = "/member-api/v1/books/reader-profile"


@tag("core")
class MemberReaderProfileApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug="main")

        cls.member = User.objects.create_user(email="owner@test.com")
        cls.member.tier = cls.main_tier
        cls.member.save()

        # A key that can write the profile, and a read-only key for the same
        # member.
        cls.write_key, cls.write_plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="agent",
            scopes=["books:read", "books:write_profile"],
        )
        cls.readonly_key, cls.readonly_plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="reader", scopes=["books:read"],
        )
        # A progress/notes automation must NOT be able to flip visibility.
        cls.progress_key, cls.progress_plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="progress bot",
            scopes=["books:read", "books:write_progress", "books:write_notes"],
        )

    def _auth(self, plaintext):
        return {"HTTP_AUTHORIZATION": f"Token {plaintext}"}

    def _put(self, plaintext, body):
        return self.client.put(
            PROFILE_URL,
            data=json.dumps(body),
            content_type="application/json",
            **self._auth(plaintext),
        )

    # ---- GET -------------------------------------------------------------

    def test_get_missing_row_reports_private(self):
        response = self.client.get(
            PROFILE_URL, **self._auth(self.readonly_plaintext),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visibility"], "private")

    def test_get_reflects_write(self):
        self._put(self.write_plaintext, {"visibility": "public"})
        response = self.client.get(
            PROFILE_URL, **self._auth(self.readonly_plaintext),
        )
        self.assertEqual(response.json()["visibility"], "public")

    # ---- PUT -------------------------------------------------------------

    def test_put_sets_visibility(self):
        response = self._put(self.write_plaintext, {"visibility": "public"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visibility"], "public")
        self.assertTrue(
            ReaderProfile.objects.filter(
                user=self.member, visibility=READER_VISIBILITY_PUBLIC,
            ).exists()
        )
        # Flip back.
        response = self._put(self.write_plaintext, {"visibility": "private"})
        self.assertEqual(response.json()["visibility"], "private")

    def test_put_invalid_value_is_400(self):
        response = self._put(self.write_plaintext, {"visibility": "secret"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_visibility")
        self.assertFalse(ReaderProfile.objects.filter(user=self.member).exists())

    def test_put_missing_value_is_400(self):
        response = self._put(self.write_plaintext, {})
        self.assertEqual(response.status_code, 400)

    # ---- Scope enforcement ----------------------------------------------

    def test_put_with_readonly_key_is_401(self):
        response = self._put(self.readonly_plaintext, {"visibility": "public"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "insufficient_scope")

    def test_put_with_progress_notes_key_is_401(self):
        # A progress/notes automation cannot change the member's posture.
        response = self._put(self.progress_plaintext, {"visibility": "public"})
        self.assertEqual(response.status_code, 401)

    def test_get_requires_a_key(self):
        response = self.client.get(PROFILE_URL)
        self.assertEqual(response.status_code, 401)

    # ---- Owner scoping ---------------------------------------------------

    def test_put_never_accepts_a_user_parameter(self):
        # A stray user field in the body must not target another member — the
        # endpoint only ever acts on the key owner.
        other = User.objects.create_user(email="other@test.com")
        other.tier = self.main_tier
        other.save()
        response = self._put(
            self.write_plaintext,
            {"visibility": "public", "user": other.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ReaderProfile.objects.filter(
                user=self.member, visibility=READER_VISIBILITY_PUBLIC,
            ).exists()
        )
        self.assertFalse(ReaderProfile.objects.filter(user=other).exists())

    # ---- OpenAPI ---------------------------------------------------------

    def test_reader_profile_in_openapi(self):
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
            "/member-api/v1/books/reader-profile",
            document["paths"],
        )
