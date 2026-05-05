"""Tests for ``POST /api/contacts/import`` (issue #431)."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import TierOverride, Token
from payments.models import Tier

User = get_user_model()


class ContactsImportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # The free tier comes from data migrations; ensure main also exists
        # (used by default_tier tests).
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20},
        )
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _post(self, payload, *, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        return self.client.post(
            "/api/contacts/import",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def test_import_creates_new_users(self):
        response = self._post({
            "contacts": [
                {"email": "alice@test.com"},
                {"email": "bob@test.com"},
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 2)
        self.assertTrue(User.objects.filter(email="alice@test.com").exists())
        self.assertTrue(User.objects.filter(email="bob@test.com").exists())

    def test_import_updates_existing_users_with_per_row_tags(self):
        existing = User.objects.create_user(
            email="existing@test.com",
            password=None,
        )
        existing.tags = ["existing"]
        existing.save(update_fields=["tags"])

        response = self._post({
            "contacts": [
                {"email": "existing@test.com", "tags": ["new-tag"]},
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)

        existing.refresh_from_db()
        # MERGE, not REPLACE: the original tag is still there.
        self.assertEqual(existing.tags, ["existing", "new-tag"])

    def test_import_with_default_tag_applies_to_every_row(self):
        response = self._post({
            "contacts": [
                {"email": "row1@test.com"},
                {"email": "row2@test.com"},
                {"email": "row3@test.com"},
            ],
            "default_tag": "campaign-q1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 3)

        for email in ("row1@test.com", "row2@test.com", "row3@test.com"):
            user = User.objects.get(email=email)
            self.assertIn("campaign-q1", user.tags)

    def test_import_with_default_tier_creates_tier_override(self):
        response = self._post({
            "contacts": [{"email": "tiered@test.com"}],
            "default_tier": "main",
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="tiered@test.com")
        override = (
            TierOverride.objects
            .filter(user=user, is_active=True)
            .select_related("override_tier")
            .first()
        )
        self.assertIsNotNone(override)
        self.assertEqual(override.override_tier.slug, "main")

    def test_import_malformed_email_is_warned_not_raised(self):
        response = self._post({
            "contacts": [
                {"email": "not-an-email"},
                {"email": "good@example.com"},
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["malformed"], 1)
        self.assertEqual(body["created"], 1)
        self.assertTrue(
            any("not-an-email" == w["value"] for w in body["warnings"]),
            f"Expected a malformed-email warning in {body['warnings']}",
        )

    def test_import_rolls_back_on_internal_error(self):
        """A failure mid-batch must not leave half-imported users behind."""
        users_before = User.objects.count()

        # Patch _apply_tag (used inside the per-row loop) to raise after the
        # decorator has already created the first user. The whole batch is
        # wrapped in transaction.atomic, so the first row's INSERT must roll
        # back together with the rest of the batch.
        with mock.patch(
            "studio.services.contacts_import._apply_tag",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self._post({
                    "contacts": [
                        {"email": "first@test.com"},
                        {"email": "second@test.com"},
                    ],
                })

        # Atomic block rolled back: no new users.
        self.assertEqual(User.objects.count(), users_before)

    def test_import_unknown_default_tier_returns_400(self):
        users_before = User.objects.count()
        response = self._post({
            "contacts": [{"email": "x@test.com"}],
            "default_tier": "nonexistent",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "unknown_tier")
        # The error fires before the import runs; no users created.
        self.assertEqual(User.objects.count(), users_before)

    def test_import_missing_contacts_key_returns_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_contacts")

    def test_import_invalid_json_returns_400(self):
        response = self._post(None, raw_body="not-json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})

    def test_import_rejects_get(self):
        response = self.client.get(
            "/api/contacts/import",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})
