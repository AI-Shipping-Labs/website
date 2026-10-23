"""A fixed legacy key can be recovered without exposing or enabling secrets."""

import json
from unittest.mock import patch

from community_base.api.models import APIKey
from community_base.config.crypto import decrypt, encrypt
from community_base.config.models import Setting, SettingChange
from community_base.config.service import runtime
from community_base.kernel.redaction import REDACTED
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.services.legacy_zoom_recovery import LEGACY_ZOOM_KEY

URL = "/api/v1/settings/recover-legacy-zoom"
SECRET = "private-replacement-secret-2000"


@override_settings(SECRET_KEY="active-test-key-2000")
class LegacyZoomRecoveryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        staff = get_user_model().objects.create_user(
            email="legacy-recovery@example.invalid", is_staff=True,
        )
        _, cls.write_key = APIKey.create_for_user(
            user=staff, name="recovery", scopes=["settings.write"], kind=APIKey.Kind.STAFF,
        )
        _, cls.read_key = APIKey.create_for_user(
            user=staff, name="read only", scopes=["settings.read"], kind=APIKey.Kind.STAFF,
        )
        with override_settings(SECRET_KEY="previous-test-key-2000"):
            cls.previous = encrypt("previous-private-secret")
        Setting.objects.create(key=LEGACY_ZOOM_KEY, value=cls.previous,
                               value_type="str", source="legacy")
        Setting.objects.create(key="ZOOM_ENABLED", value=False,
                               value_type="bool", source="studio")

    def tearDown(self):
        runtime.reset()

    def _post(self, payload=None, key=None):
        if payload is None:
            payload = {"value": SECRET}
        if key is None:
            key = self.write_key
        return self.client.post(URL, json.dumps(payload), content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {key}")

    def test_requires_bearer_settings_write_scope(self):
        for authorization, expected in (("", 401), ("Bearer invalid", 401),
                                        (f"Bearer {self.read_key}", 403)):
            with self.subTest(expected=expected):
                response = self.client.post(URL, {"value": SECRET},
                                            content_type="application/json",
                                            HTTP_AUTHORIZATION=authorization)
                self.assertEqual(response.status_code, expected)
        self.assertEqual(Setting.objects.get(key=LEGACY_ZOOM_KEY).value, self.previous)
        self.assertFalse(SettingChange.objects.exists())

    def test_recovers_with_boolean_proof_redacted_audit_and_no_other_writes(self):
        other = list(Setting.objects.exclude(key=LEGACY_ZOOM_KEY).values())
        with patch.object(runtime, "publish") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._post()
            self.assertEqual(publish.call_count, 1)
        self.assertEqual(response.json(), {"recovered": True, "decryptable": True})
        row = Setting.objects.get(key=LEGACY_ZOOM_KEY)
        self.assertEqual(decrypt(row.value), SECRET)
        self.assertNotEqual(row.value, self.previous)
        self.assertEqual(list(Setting.objects.exclude(key=LEGACY_ZOOM_KEY).values()), other)
        audit = SettingChange.objects.get(setting_key=LEGACY_ZOOM_KEY)
        self.assertEqual(audit.old_value, REDACTED)
        self.assertEqual(audit.new_value, REDACTED)
        self.assertTrue(audit.old_value_redacted)
        self.assertTrue(audit.new_value_redacted)
        for private in (SECRET, self.previous, row.value, "previous-private-secret"):
            self.assertNotIn(private, str(list(SettingChange.objects.values())))
            self.assertNotIn(private, response.content.decode())

    def test_repeat_recovery_is_idempotent(self):
        self.assertEqual(self._post().json(), {"recovered": True, "decryptable": True})
        before = list(Setting.objects.values())
        self.assertEqual(self._post().json(), {"recovered": True, "decryptable": True})
        self.assertEqual(list(Setting.objects.values()), before)
        self.assertEqual(SettingChange.objects.count(), 1)

    def test_rejects_empty_malformed_or_arbitrary_key_input(self):
        before = list(Setting.objects.values())
        for payload in ({}, {"value": ""}, {"value": "  "}, {"value": False},
                        {"value": None}, {"value": []},
                        {"value": SECRET, "key": "ZOOM_ENABLED"}):
            with self.subTest(payload_type=type(payload).__name__):
                response = self._post(payload)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(SECRET, response.content.decode())
        response = self.client.post(URL, "{", content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {self.write_key}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(Setting.objects.values()), before)
        self.assertFalse(SettingChange.objects.exists())

    def test_missing_legacy_row_does_not_create_or_change_settings(self):
        Setting.objects.filter(key=LEGACY_ZOOM_KEY).delete()
        before = list(Setting.objects.values())
        self.assertEqual(self._post().status_code, 404)
        self.assertEqual(list(Setting.objects.values()), before)
        self.assertFalse(SettingChange.objects.exists())

    def test_non_post_methods_cannot_write(self):
        before = list(Setting.objects.values())
        for method in ("get", "put", "patch", "delete", "head", "options"):
            response = getattr(self.client, method)(
                URL, HTTP_AUTHORIZATION=f"Bearer {self.write_key}",
            )
            self.assertEqual(response.status_code, 405)
        self.assertEqual(list(Setting.objects.values()), before)
        self.assertFalse(SettingChange.objects.exists())

    @patch("integrations.services.legacy_zoom_recovery.encrypt", return_value="bad ciphertext")
    def test_failed_stored_decryption_proof_rolls_back(self, encrypt_mock):
        before = list(Setting.objects.values())
        with patch.object(runtime, "publish") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._post()
            self.assertEqual(publish.call_count, 0)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "recovery_failed")
        self.assertNotIn(SECRET, response.content.decode())
        self.assertEqual(list(Setting.objects.values()), before)
        self.assertFalse(SettingChange.objects.exists())
