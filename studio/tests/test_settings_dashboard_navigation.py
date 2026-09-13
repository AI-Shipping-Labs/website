"""Navigation and access contracts for package settings."""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class SettingsDashboardNavigationShellTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="settings-nav@test.com", password="testpass", is_staff=True)

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")

    def test_dashboard_renders_package_sections_and_filterable_fields(self):
        response = self.client.get("/studio/settings/")
        body = response.content.decode()

        self.assertIn("data-community-settings", body)
        self.assertIn('data-settings-section="slack"', body)
        self.assertIn('data-settings-section="stripe"', body)
        self.assertIn('id="id_SLACK_BOT_TOKEN"', body)


class SettingsDashboardAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email="settings-member@test.com", password="testpass")

    def test_anonymous_user_is_redirected(self):
        response = self.client.get("/studio/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_non_staff_user_gets_403_without_settings_keys(self):
        self.client.login(email=self.member.email, password="testpass")
        response = self.client.get("/studio/settings/")
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "SLACK_BOT_TOKEN", status_code=403)
