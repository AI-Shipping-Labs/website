"""Contract tests for the package-owned runtime settings API (#1584).

The old ``/api/integrations/settings`` endpoint was retired with the donor
implementation. Runtime configuration is now served by community-base under
``/api/v1/settings`` with bearer API keys, encrypted package storage, and
redacted exports.
"""

import json
from http import HTTPStatus

from community_base.api.models import APIKey
from community_base.config.crypto import PREFIX, decrypt, encrypt
from community_base.config.models import Setting
from community_base.kernel.redaction import REDACTED
from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class CommunitySettingsApiTest(TestCase):
    success_status = HTTPStatus.OK

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-package-api@test.com", password="pw", is_staff=True
        )
        cls.member = User.objects.create_user(
            email="member-package-api@test.com", password="pw"
        )
        cls.api_key, cls.plaintext = APIKey.create_for_user(
            user=cls.staff,
            name="settings API",
            scopes=["settings.read", "settings.write"],
            kind=APIKey.Kind.STAFF,
        )
        cls.member_key, cls.member_plaintext = APIKey.create_for_user(
            user=cls.member,
            name="member API",
            scopes=["settings.read", "settings.write"],
            kind=APIKey.Kind.MEMBER,
        )

    def _headers(self, token=None):
        return {"HTTP_AUTHORIZATION": f"Bearer {token or self.plaintext}"}

    def _json(self, method, path, payload=None, token=None):
        return getattr(self.client, method)(
            path,
            data=None if payload is None else json.dumps(payload),
            content_type="application/json",
            **self._headers(token),
        )

    def test_donor_route_is_retired(self):
        response = self.client.get("/api/integrations/settings")
        self.assertEqual(response.status_code, 404)

    def test_requires_bearer_key(self):
        self.assertEqual(self.client.get("/api/v1/settings").status_code, 401)
        response = self.client.get(
            "/api/v1/settings", **self._headers(self.member_plaintext)
        )
        self.assertEqual(response.status_code, self.success_status)

    def test_list_returns_metadata_without_secret_value(self):
        secret = "package-api-secret-must-not-leak"
        Setting.objects.create(
            key="STRIPE_SECRET_KEY",
            value=encrypt(secret),
            value_type="str",
            source="api",
        )

        response = self.client.get(
            "/api/v1/settings/STRIPE_SECRET_KEY", **self._headers()
        )
        self.assertEqual(response.status_code, self.success_status)
        body = response.json()
        entry = body
        self.assertEqual(entry["value"], REDACTED)
        self.assertNotIn(secret, response.content.decode())

    def test_put_writes_package_storage_and_round_trips(self):
        response = self._json(
            "put",
            "/api/v1/settings/SITE_BASE_URL",
            {"value": "https://package-api.example.test"},
        )
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(response.json()["value"], "https://package-api.example.test")
        row = Setting.objects.get(key="SITE_BASE_URL")
        self.assertEqual(row.source, "api")
        self.assertEqual(row.value, "https://package-api.example.test")

    def test_put_encrypts_secrets_and_get_redacts_them(self):
        secret = "package-api-secret-stored-encrypted"
        response = self._json(
            "put", "/api/v1/settings/STRIPE_SECRET_KEY", {"value": secret}
        )
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(response.json()["value"], REDACTED)
        row = Setting.objects.get(key="STRIPE_SECRET_KEY")
        self.assertTrue(row.value.startswith(PREFIX))
        self.assertEqual(decrypt(row.value), secret)
        self.assertNotIn(secret, response.content.decode())

    def test_export_redacts_secrets_and_import_skips_redacted_values(self):
        secret = "export-secret-never-plaintext"
        self._json("put", "/api/v1/settings/STRIPE_SECRET_KEY", {"value": secret})
        response = self.client.get("/api/v1/settings/export", **self._headers())
        self.assertEqual(response.status_code, self.success_status)
        self.assertNotIn(secret, response.content.decode())
        self.assertEqual(response.json()["settings"]["STRIPE_SECRET_KEY"], REDACTED)

        response = self._json(
            "post",
            "/api/v1/settings/import",
            {"settings": {"STRIPE_SECRET_KEY": REDACTED}},
        )
        self.assertEqual(response.status_code, self.success_status)
        self.assertEqual(decrypt(Setting.objects.get(key="STRIPE_SECRET_KEY").value), secret)
