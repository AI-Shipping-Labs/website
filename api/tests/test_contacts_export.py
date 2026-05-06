"""Tests for ``GET /api/contacts/export`` (issue #431)."""

import csv
import io

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from payments.models import Tier

User = get_user_model()


class ContactsExportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def _get(self, path):
        return self.client.get(
            path,
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def test_export_returns_all_users_in_id_order(self):
        u1 = User.objects.create_user(email="alpha@test.com", password=None)
        u2 = User.objects.create_user(email="beta@test.com", password=None)
        u3 = User.objects.create_user(email="gamma@test.com", password=None)

        response = self._get("/api/contacts/export")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # admin + 3 users created above = 4 contacts total.
        self.assertEqual(len(body["contacts"]), 4)

        # Ordered ascending by id, so the admin (created first in
        # setUpTestData) comes first, then u1, u2, u3.
        emails_in_order = [c["email"] for c in body["contacts"]]
        self.assertEqual(
            emails_in_order,
            [
                self.admin.email,
                u1.email,
                u2.email,
                u3.email,
            ],
        )

    def test_export_columns_match_spec(self):
        response = self._get("/api/contacts/export")
        body = response.json()
        contact = body["contacts"][0]
        self.assertEqual(
            set(contact.keys()),
            {
                "email",
                "first_name",
                "last_name",
                "tags",
                "tier",
                "email_verified",
                "unsubscribed",
                "date_joined",
                "last_login",
                "stripe_customer_id",
                "subscription_id",
                "slack_member",
                "slack_checked_at",
            },
        )

    def test_export_csv_format_returns_csv_content_type(self):
        User.objects.create_user(email="csv@test.com", password=None)
        response = self._get("/api/contacts/export?format=csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        disposition = response["Content-Disposition"]
        self.assertIn('attachment; filename="aishippinglabs-contacts-', disposition)
        self.assertTrue(disposition.endswith('.csv"'))

        # The CSV header row matches the export columns spec.
        text = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        self.assertEqual(
            header,
            [
                "email",
                "first_name",
                "last_name",
                "tags",
                "tier",
                "email_verified",
                "unsubscribed",
                "date_joined",
                "last_login",
                "stripe_customer_id",
                "subscription_id",
                "slack_member",
                "slack_checked_at",
            ],
        )

    def test_export_includes_user_with_no_tier(self):
        u = User.objects.create_user(email="notier@test.com", password=None)
        # Force tier_id to NULL bypassing the User.save() free-tier default.
        User.objects.filter(pk=u.pk).update(tier=None)

        response = self._get("/api/contacts/export")
        body = response.json()
        match = next(c for c in body["contacts"] if c["email"] == "notier@test.com")
        self.assertEqual(match["tier"], "free")

    def test_export_serializes_null_last_login_as_null(self):
        # ``create_user`` does not set ``last_login`` so it stays NULL.
        u = User.objects.create_user(email="never@test.com", password=None)
        self.assertIsNone(u.last_login)

        response = self._get("/api/contacts/export")
        body = response.json()
        match = next(c for c in body["contacts"] if c["email"] == "never@test.com")
        self.assertIsNone(match["last_login"])

    def test_export_rejects_post(self):
        response = self.client.post(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(response.status_code, 405)


class ContactsExportTierResolutionTest(TestCase):
    """Tier slug serialization: pulls user.tier.slug straight, no overrides."""

    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20}
        )
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin)

    def test_main_tier_user_serializes_main(self):
        u = User.objects.create_user(email="paid@test.com", password=None)
        u.tier = Tier.objects.get(slug="main")
        u.save()

        response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        body = response.json()
        match = next(c for c in body["contacts"] if c["email"] == "paid@test.com")
        self.assertEqual(match["tier"], "main")
