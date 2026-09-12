"""Package-owned observability settings contracts."""

from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config

User = get_user_model()
TOKEN = "logfire-package-token"


class SettingsDashboardObservabilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="observability-staff@test.com", password="testpass", is_staff=True)

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")
        clear_config_cache()

    def tearDown(self):
        Setting.objects.filter(key__in=("LOGFIRE_ENABLED", "LOGFIRE_TOKEN", "LOGFIRE_ENVIRONMENT")).delete()
        clear_config_cache()

    def test_staff_sees_observability_group_with_registered_keys(self):
        response = self.client.get("/studio/settings/")

        self.assertContains(response, 'data-settings-section="observability"')
        for key in ("LOGFIRE_ENABLED", "LOGFIRE_TOKEN", "LOGFIRE_ENVIRONMENT"):
            self.assertContains(response, key)

    def test_token_is_masked_and_runtime_resolver_can_read_package_secret(self):
        package_set("LOGFIRE_TOKEN", TOKEN, actor_ref="test:1584")
        response = self.client.get("/studio/settings/")

        self.assertNotIn(TOKEN, response.content.decode())
        self.assertNotEqual(Setting.objects.get(key="LOGFIRE_TOKEN").value, TOKEN)
        clear_config_cache()
        self.assertEqual(get_config("LOGFIRE_TOKEN"), TOKEN)

    def test_package_boolean_setting_round_trips(self):
        package_set("LOGFIRE_ENABLED", True, actor_ref="test:1584")
        clear_config_cache()

        self.assertEqual(get_config("LOGFIRE_ENABLED"), "true")
        self.assertTrue(Setting.objects.filter(key="LOGFIRE_ENABLED").exists())

    def test_logfire_save_reports_restart_requirement(self):
        response = self.client.post(
            "/studio/settings/observability/save/",
            {
                "LOGFIRE_ENABLED": "on",
                "LOGFIRE_TOKEN": TOKEN,
                "LOGFIRE_ENVIRONMENT": "test",
            },
            follow=True,
        )
        rendered_messages = " ".join(str(message) for message in response.context["messages"])

        self.assertIn("Restart the application", rendered_messages)

    def test_logfire_disable_reports_restart_requirement(self):
        package_set("LOGFIRE_ENABLED", True, actor_ref="test:1584")
        response = self.client.post(
            "/studio/settings/observability/save/",
            {"LOGFIRE_ENVIRONMENT": "production"},
            follow=True,
        )
        rendered_messages = " ".join(str(message) for message in response.context["messages"])

        self.assertIn("Restart the application", rendered_messages)

    def test_unrelated_settings_save_has_no_restart_warning(self):
        response = self.client.post(
            "/studio/settings/analytics/save/",
            {
                "GOOGLE_ANALYTICS_ID": "analytics-test",
                "USER_ACTIVITY_RETENTION_DAYS": "30",
            },
            follow=True,
        )
        rendered_messages = " ".join(str(message) for message in response.context["messages"])

        self.assertNotIn("Restart the application", rendered_messages)
