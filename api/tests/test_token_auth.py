"""Tests for ``accounts.auth.token_required`` (issue #431) and the
``staff_session_or_token_required`` composite helper (issue #736)."""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.auth import staff_session_or_token_required
from accounts.models import Token

User = get_user_model()


class TokenAuthTest(TestCase):
    """End-to-end coverage of the token auth decorator on a real endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def test_missing_authorization_header_returns_401_with_json_shape(self):
        users_before = User.objects.count()
        response = self.client.post(
            "/api/contacts/import",
            data=json.dumps({"contacts": [{"email": "x@test.com"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )
        # Per Rule 12 of testing-guidelines.md: an unauthorized request must
        # not have any side effects.
        self.assertEqual(User.objects.count(), users_before)

    def test_invalid_token_returns_401_invalid_token(self):
        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION="Token does-not-exist",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_valid_token_authenticates_request(self):
        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 200)

    def test_token_row_stores_hash_not_plaintext_key(self):
        plaintext = self.token.key
        token = Token.objects.get(pk=self.token.pk)
        self.assertIsNone(token.key)
        self.assertNotEqual(token.pk, plaintext)
        self.assertNotEqual(token.key_hash, plaintext)
        self.assertNotEqual(token.lookup_prefix, plaintext)
        self.assertEqual(
            token.lookup_prefix,
            plaintext[:Token.LOOKUP_PREFIX_LENGTH],
        )
        self.assertTrue(check_password(plaintext, token.key_hash))

    def test_lookup_prefix_collision_checks_all_hash_candidates(self):
        prefix = "same-prefix-for-auth0000"
        self.assertEqual(len(prefix), Token.LOOKUP_PREFIX_LENGTH)
        first_key = f"{prefix}first"
        second_key = f"{prefix}second"
        first = Token(key=first_key, user=self.admin, name="first-collision")
        second = Token(key=second_key, user=self.admin, name="second-collision")
        Token.objects.bulk_create([first, second])

        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {second_key}",
        )

        self.assertEqual(response.status_code, 200)
        second.refresh_from_db()
        self.assertIsNotNone(second.last_used_at)

    def test_existing_non_staff_token_row_is_rejected(self):
        member = User.objects.create_user(
            email="stale-member-token@test.com",
            password="testpass",
        )
        stale_token = Token(
            key="stale-member-token-key",
            user=member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([stale_token])

        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {stale_token.key}",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_token_save_generates_key_when_blank(self):
        token = Token(user=self.admin)
        token.save()
        first_key = token.key
        self.assertGreater(len(first_key), 30)

        # Re-saving an instance with an existing key must preserve it --
        # otherwise refreshing ``last_used_at`` on an authenticated call
        # would silently rotate the token key out from under the client.
        token.save()
        self.assertEqual(token.key, first_key)

    def test_token_creation_rejects_non_staff_user(self):
        member = User.objects.create_user(
            email="member-token@test.com",
            password="testpass",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "API tokens can only be created for staff or admin users.",
        ):
            Token.objects.create(user=member, name="not-allowed")

    def test_valid_token_updates_last_used_at(self):
        fresh_token = Token.objects.create(user=self.admin)
        self.assertIsNone(fresh_token.last_used_at)
        original_hash = fresh_token.key_hash
        original_prefix = fresh_token.lookup_prefix

        before = timezone.now()
        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {fresh_token.key}",
        )
        self.assertEqual(response.status_code, 200)

        fresh_token.refresh_from_db()
        self.assertIsNotNone(fresh_token.last_used_at)
        # Bumped during the request, so it sits between "before" and "now".
        self.assertGreaterEqual(fresh_token.last_used_at, before)
        self.assertLessEqual(fresh_token.last_used_at, timezone.now())
        self.assertEqual(fresh_token.key_hash, original_hash)
        self.assertEqual(fresh_token.lookup_prefix, original_prefix)

    def test_authorization_without_token_scheme_returns_401(self):
        """A bare key with no 'Token ' scheme is treated as missing.

        We deliberately don't accept ``Authorization: <key>`` because the
        client almost certainly forgot the scheme; failing loudly is better
        than silently accepting a confusing format.
        """
        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=self.token.key,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )


class StaffSessionOrTokenRequiredTest(TestCase):
    """Direct tests of ``staff_session_or_token_required`` (issue #736).

    Exercises the helper against a stub view so failures point at the
    composition logic itself, not at the OpenAPI doc route that consumes
    it. The integration tests on ``/api/openapi.json`` cover the wiring
    end-to-end; this class isolates the helper's auth matrix.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="helper-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="helper-member@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="helper-tok")
        # Construct directly to bypass the manager's staff-only validator
        # and model the legacy-demoted-user case.
        cls.non_staff_token = Token(
            key="helper-non-staff-key",
            user=cls.member,
            name="helper-legacy",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def setUp(self):
        self.factory = RequestFactory()
        # Stub view that returns a sentinel response so we can tell the
        # wrapped view ran vs. the helper short-circuited.
        self.sentinel_body = b"ok-from-stub"

        def stub(request):
            return HttpResponse(self.sentinel_body, status=200)

        self.wrapped = staff_session_or_token_required(stub)

    def _request(self, **extra):
        # ``RequestFactory`` does not run middleware, so ``request.user``
        # has to be set by the caller. ``user_passes_test`` reads it
        # directly, which is enough for these tests.
        return self.factory.get("/some/path", **extra)

    def test_staff_session_no_header_returns_view_response(self):
        request = self._request()
        request.user = self.staff
        response = self.wrapped(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.sentinel_body)

    def test_staff_token_returns_view_response_and_bumps_last_used_at(self):
        fresh = Token.objects.create(user=self.staff, name="helper-bump")
        self.assertIsNone(fresh.last_used_at)
        before = timezone.now()

        request = self._request(HTTP_AUTHORIZATION=f"Token {fresh.key}")
        # The user attribute is irrelevant on the token path; the helper
        # must NOT consult it. Setting an anonymous user proves the
        # header takes priority.
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        response = self.wrapped(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.sentinel_body)
        fresh.refresh_from_db()
        self.assertIsNotNone(fresh.last_used_at)
        self.assertGreaterEqual(fresh.last_used_at, before)

    def test_non_staff_session_no_header_returns_403(self):
        request = self._request()
        request.user = self.member
        response = self.wrapped(request)
        self.assertEqual(response.status_code, 403)

    def test_non_staff_token_returns_401_invalid_token(self):
        request = self._request(
            HTTP_AUTHORIZATION=f"Token {self.non_staff_token.key}",
        )
        # Mirror the production wiring: anonymous user with a header.
        # The helper must route to ``token_required`` and never touch
        # ``request.user``.
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        response = self.wrapped(request)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content), {"error": "Invalid token"})

    def test_anonymous_no_header_redirects_to_login(self):
        from django.contrib.auth.models import AnonymousUser
        request = self._request()
        request.user = AnonymousUser()
        response = self.wrapped(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_malformed_authorization_header_returns_401(self):
        # The Authorization header is present but doesn't carry the
        # ``Token`` scheme, so the helper must NOT fall back to the
        # session-redirect path -- it must surface the existing
        # ``token_required`` 401 shape so API clients aren't redirected
        # to a browser login page.
        from django.contrib.auth.models import AnonymousUser
        request = self._request(HTTP_AUTHORIZATION="Bearer xyz")
        request.user = AnonymousUser()
        response = self.wrapped(request)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            json.loads(response.content),
            {"error": "Authentication token required"},
        )
