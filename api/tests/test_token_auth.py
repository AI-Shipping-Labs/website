"""Tests for ``accounts.auth.token_required`` (issue #431)."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

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
