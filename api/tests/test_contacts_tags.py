"""Tests for ``POST /api/contacts/<email>/tags`` (issue #431)."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token

User = get_user_model()


class ContactsSetTagsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin)

    def _post(self, email, payload, *, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        return self.client.post(
            f"/api/contacts/{email}/tags",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def test_set_tags_replaces_existing_tags(self):
        user = User.objects.create_user(email="rep@test.com", password=None)
        user.tags = ["old-1", "old-2"]
        user.save(update_fields=["tags"])

        response = self._post("rep@test.com", {"tags": ["new-1", "new-2"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"email": "rep@test.com", "tags": ["new-1", "new-2"]},
        )

        user.refresh_from_db()
        # REPLACE, not merge: old tags are gone.
        self.assertEqual(user.tags, ["new-1", "new-2"])

    def test_set_tags_normalizes_input(self):
        User.objects.create_user(email="norm@test.com", password=None)
        response = self._post(
            "norm@test.com",
            {"tags": ["My Cool Tag", "BAD!chars", "  spaced  "]},
        )
        self.assertEqual(response.status_code, 200)
        # Slugified by accounts.utils.tags.normalize_tag(s):
        # - "My Cool Tag" -> "my-cool-tag"
        # - "BAD!chars" -> "badchars"
        # - "  spaced  " -> "spaced"
        self.assertEqual(
            response.json()["tags"],
            ["my-cool-tag", "badchars", "spaced"],
        )

    def test_set_empty_list_clears_tags(self):
        user = User.objects.create_user(email="clear@test.com", password=None)
        user.tags = ["existing"]
        user.save(update_fields=["tags"])

        response = self._post("clear@test.com", {"tags": []})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.tags, [])

    def test_set_tags_unknown_email_returns_404(self):
        response = self._post("nobody@example.com", {"tags": ["x"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "contact_not_found")

    def test_set_tags_missing_tags_key_returns_400(self):
        User.objects.create_user(email="missing@test.com", password=None)
        response = self._post("missing@test.com", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "missing_tags")

    def test_set_tags_rejects_non_list_value(self):
        User.objects.create_user(email="nonlist@test.com", password=None)
        response = self._post("nonlist@test.com", {"tags": "single-tag"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "missing_tags")

    def test_set_tags_rejects_get(self):
        User.objects.create_user(email="get@test.com", password=None)
        response = self.client.get(
            "/api/contacts/get@test.com/tags",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)

    def test_set_tags_unauthenticated_returns_401_no_side_effects(self):
        user = User.objects.create_user(email="unauth@test.com", password=None)
        user.tags = ["original"]
        user.save(update_fields=["tags"])

        response = self.client.post(
            "/api/contacts/unauth@test.com/tags",
            data=json.dumps({"tags": ["hijacked"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

        # Per Rule 12: verify the side effect did NOT happen.
        user.refresh_from_db()
        self.assertEqual(user.tags, ["original"])
