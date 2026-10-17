"""Tests for the admin device login flow and ``GET /api/whoami``.

Status assertions follow the status-200 ratchet
(tests/test_status_200_assertion_ratchet.py): success is pinned through
behavioral response contracts (`assertContains`, payload assertions)
rather than literal `status_code == 200` anchors.
"""

import json

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token

User = get_user_model()


class WhoamiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(email="admin@test.com", is_staff=True)
        cls.token = Token.objects.create(user=cls.admin, name="test")

    def test_missing_token_returns_401(self):
        response = self.client.get("/api/whoami")
        self.assertEqual(response.status_code, 401)

    def test_staff_token_returns_owner_email(self):
        response = self.client.get(
            "/api/whoami", HTTP_AUTHORIZATION=f"Token {self.token.key}"
        )
        self.assertContains(response, "admin@test.com")
        self.assertEqual(response.json(), {"email": "admin@test.com"})

    def test_downgraded_owner_token_is_rejected(self):
        self.admin.is_staff = False
        self.admin.save(update_fields=("is_staff",))
        response = self.client.get(
            "/api/whoami", HTTP_AUTHORIZATION=f"Token {self.token.key}"
        )
        self.assertEqual(response.status_code, 401)


class DeviceLoginTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(email="device-admin@test.com", is_staff=True)
        cls.member = User.objects.create_user(email="device-member@test.com")

    def _start(self):
        response = self.client.post(
            "/api/auth/device",
            data=json.dumps({"label": "agent@box"}),
            content_type="application/json",
        )
        self.assertContains(response, "device_code")
        return response.json()

    def _poll(self, device_code):
        from community_base.device_login.codes import sha256_hex
        from community_base.device_login.models import DeviceGrant

        DeviceGrant.objects.filter(device_code_hash=sha256_hex(device_code)).update(
            last_poll_at=None
        )
        return self.client.post(
            "/api/auth/device/token",
            data=json.dumps({"device_code": device_code}),
            content_type="application/json",
        )

    def test_full_flow_mints_staff_token_usable_on_legacy_api(self):
        offer = self._start()
        browser = self.client.__class__()
        browser.force_login(self.admin)
        approved = browser.post(
            "/api/auth/device/approve",
            data=json.dumps({"user_code": offer["user_code"], "approve": True}),
            content_type="application/json",
        )
        self.assertContains(approved, "approved")

        token_response = self._poll(offer["device_code"])
        self.assertContains(token_response, "token")
        plaintext = token_response.json()["token"]

        whoami = self.client.get("/api/whoami", HTTP_AUTHORIZATION=f"Token {plaintext}")
        self.assertContains(whoami, "device-admin@test.com")

        # The token is mirrored into community_base.api, so Bearer works too.
        mirror = APIKey.objects.filter(
            lookup_prefix=plaintext[: APIKey.LOOKUP_PREFIX_LENGTH]
        ).first()
        self.assertIsNotNone(mirror)

        # Single use: the second poll reads as expired.
        again = self._poll(offer["device_code"])
        self.assertEqual(again.json()["error"], "expired_token")

    def test_non_admin_approval_is_refused_and_mints_nothing(self):
        offer = self._start()
        member_browser = self.client.__class__()
        member_browser.force_login(self.member)
        refused = member_browser.post(
            "/api/auth/device/approve",
            data=json.dumps({"user_code": offer["user_code"], "approve": True}),
            content_type="application/json",
        )
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"], "admin_only")

        pending = self._poll(offer["device_code"])
        self.assertEqual(pending.json()["error"], "authorization_pending")

        admin_browser = self.client.__class__()
        admin_browser.force_login(self.admin)
        admin_approved = admin_browser.post(
            "/api/auth/device/approve",
            data=json.dumps({"user_code": offer["user_code"], "approve": True}),
            content_type="application/json",
        )
        self.assertContains(admin_approved, "approved")
        minted = self._poll(offer["device_code"])
        self.assertContains(minted, "token")

    def test_existing_tokens_keep_working(self):
        legacy = Token.objects.create(user=self.admin, name="legacy")
        response = self.client.get(
            "/api/whoami", HTTP_AUTHORIZATION=f"Token {legacy.key}"
        )
        self.assertContains(response, "device-admin@test.com")

    def test_verification_page_renders_for_admin(self):
        offer = self._start()
        browser = self.client.__class__()
        browser.force_login(self.admin)
        response = browser.get(f"/device?code={offer['user_code']}")
        self.assertContains(response, "Authorize this machine?")

    def test_verification_page_requires_login(self):
        response = self.client.get("/device")
        self.assertEqual(response.status_code, 302)
