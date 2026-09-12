"""Package settings contracts migrated from issue #1268."""

import json
from pathlib import Path

from community_base.api.models import APIKey
from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from email_app.tasks.send_campaign import _get_batch_size
from integrations.config import clear_config_cache
from integrations.services.llm.backends import _resolve_max_retries
from integrations.settings_registry import get_group_by_name
from payments.stripe_links import get_stripe_payment_links

User = get_user_model()

LINKS = {
    tier: {period: f"https://runtime.test/{tier}/{period}" for period in ("monthly", "annual")}
    for tier in ("basic", "main", "premium")
}
KEYS = {
    "stripe": "STRIPE_PAYMENT_LINKS",
    "ses": "EMAIL_BATCH_SIZE",
    "llm": "LLM_MAX_RETRIES",
}


class RuntimeSettingsRegistryTest(SimpleTestCase):
    def test_registry_metadata_and_documentation_anchors(self):
        expectations = {
            "stripe": ("STRIPE_PAYMENT_LINKS", "stripe.md", True, None),
            "ses": ("EMAIL_BATCH_SIZE", "ses.md", False, "200"),
            "llm": ("LLM_MAX_RETRIES", "llm.md", False, "6"),
        }
        for group_name, (key, filename, multiline, default) in expectations.items():
            with self.subTest(key=key):
                entry = next(item for item in get_group_by_name(group_name)["keys"] if item["key"] == key)
                self.assertFalse(entry["is_secret"])
                self.assertTrue(entry["optional"])
                self.assertTrue(entry["description"])
                self.assertEqual(entry.get("multiline", False), multiline)
                if default is not None:
                    self.assertEqual(entry["default"], default)
                self.assertEqual(entry["docs_url"], f"_docs/integrations/{filename}#{key.lower()}")
                docs = (Path(settings.BASE_DIR) / "_docs" / "integrations" / filename).read_text()
                self.assertIn(f"## {key}\n", docs)


class RuntimeSettingsStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="runtime-settings-staff@test.com", password="testpass", is_staff=True
        )

    def setUp(self):
        clear_config_cache()
        self.client.login(email=self.staff.email, password="testpass")

    def tearDown(self):
        Setting.objects.filter(key__in=KEYS.values()).delete()
        clear_config_cache()

    def test_fields_render_with_controls_docs_and_source_badges(self):
        package_set("STRIPE_PAYMENT_LINKS", LINKS, actor_ref="test:1268")
        response = self.client.get("/studio/settings/")

        for key in KEYS.values():
            self.assertContains(response, f'id="id_{key}"')
        stripe_group = next(group for group in response.context["groups"] if group["name"] == "stripe")
        stripe_setting = next(item for item in stripe_group["settings"] if item["key"] == "STRIPE_PAYMENT_LINKS")
        self.assertEqual(stripe_setting["source"], "db")
        self.assertContains(response, "Documentation")

    def test_studio_save_refreshes_package_runtime_values(self):
        response = self.client.post("/studio/settings/stripe/save/", {
            "STRIPE_CUSTOMER_PORTAL_URL": "https://runtime.test/portal",
            "STRIPE_DASHBOARD_ACCOUNT_ID": "acct_runtime",
            "STRIPE_PAYMENT_LINKS": json.dumps(LINKS),
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Setting.objects.filter(key="STRIPE_PAYMENT_LINKS").exists())
        self.assertEqual(get_stripe_payment_links(), LINKS)

        package_set("EMAIL_BATCH_SIZE", 17, actor_ref="test:1268")
        package_set("LLM_MAX_RETRIES", 2, actor_ref="test:1268")
        clear_config_cache()
        self.assertEqual(_get_batch_size(), 17)
        self.assertEqual(_resolve_max_retries(), 2)


class RuntimeSettingsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="runtime-settings-api@test.com", is_staff=True)
        _, cls.plaintext = APIKey.create_for_user(
            user=cls.staff,
            name="runtime-settings",
            scopes=["settings.read", "settings.write"],
            kind=APIKey.Kind.STAFF,
        )

    def setUp(self):
        clear_config_cache()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.plaintext}"}

    def tearDown(self):
        Setting.objects.filter(key__in=KEYS.values()).delete()
        clear_config_cache()

    def test_get_reads_package_setting_without_echoing_secret_data(self):
        package_set("EMAIL_BATCH_SIZE", 19, actor_ref="test:1268")
        response = self.client.get("/api/v1/settings/EMAIL_BATCH_SIZE", **self.auth)

        self.assertEqual(response.json()["value"], 19)

    def test_put_updates_package_setting(self):
        self.client.put(
            "/api/v1/settings/LLM_MAX_RETRIES",
            data=json.dumps({"value": 3}),
            content_type="application/json",
            **self.auth,
        )

        self.assertEqual(Setting.objects.get(key="LLM_MAX_RETRIES").value, 3)
