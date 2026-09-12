"""Package settings transfer contracts."""

import json

from community_base.config.crypto import PREFIX, decrypt
from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from studio.services.settings_io import apply_import, build_export

User = get_user_model()


class SettingsTransferTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="transfer-staff@test.com", password="testpass", is_staff=True
        )
        cls.member = User.objects.create_user(email="transfer-member@test.com", password="testpass")

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")

    def tearDown(self):
        Setting.objects.filter(key__in=("SITE_BASE_URL", "STRIPE_SECRET_KEY", "EMAIL_BATCH_SIZE")).delete()

    def test_dashboard_links_to_package_transfer_endpoints(self):
        response = self.client.get("/studio/settings/")

        self.assertContains(response, f'href="{reverse("community_base_settings_export")}"')
        self.assertContains(response, f'action="{reverse("community_base_settings_import")}"')

    def test_compatibility_export_uses_package_values_and_redacts_secret(self):
        package_set("SITE_BASE_URL", "https://transfer.example.test", actor_ref="test:1584")
        package_set("STRIPE_SECRET_KEY", "transfer-secret", actor_ref="test:1584")

        payload = build_export()
        exported = json.dumps(payload)
        values = {item["key"]: item["value"] for item in payload["integration_settings"]}

        self.assertEqual(values["SITE_BASE_URL"], "https://transfer.example.test")
        self.assertEqual(values["STRIPE_SECRET_KEY"], "[REDACTED]")
        self.assertNotIn("transfer-secret", exported)

    def test_import_writes_package_storage_and_preserves_redacted_secret(self):
        package_set("STRIPE_SECRET_KEY", "existing-secret", actor_ref="test:1584")
        result = apply_import({
            "format_version": 1,
            "integration_settings": [
                {"key": "SITE_BASE_URL", "value": "https://import.example.test"},
                {"key": "STRIPE_SECRET_KEY", "value": "[REDACTED]"},
            ],
            "auth_providers": [],
        })

        self.assertEqual(result.integration_created, 1)
        self.assertEqual(Setting.objects.get(key="SITE_BASE_URL").value, "https://import.example.test")
        secret_row = Setting.objects.get(key="STRIPE_SECRET_KEY")
        self.assertTrue(secret_row.value.startswith(PREFIX))
        self.assertEqual(decrypt(secret_row.value), "existing-secret")

    def test_anonymous_and_non_staff_cannot_export(self):
        self.client.logout()
        self.assertEqual(self.client.get("/studio/settings/export/").status_code, 302)
        self.client.login(email=self.member.email, password="testpass")
        self.assertEqual(self.client.get("/studio/settings/export/").status_code, 403)
