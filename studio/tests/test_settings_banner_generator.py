"""Package-owned banner generator settings coverage."""

from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache
from integrations.services.banner_generator import is_enabled

User = get_user_model()


class SettingsDashboardBannerGeneratorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="banner-staff@test.com", password="testpass", is_staff=True
        )
        cls.member = User.objects.create_user(email="banner-member@test.com", password="testpass")

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        Setting.objects.filter(key__startswith="BANNER_GENERATOR_").delete()
        clear_config_cache()

    def test_anonymous_redirects_and_non_staff_is_forbidden(self):
        self.assertEqual(self.client.get("/studio/settings/").status_code, 302)
        self.client.login(email=self.member.email, password="testpass")
        self.assertEqual(self.client.get("/studio/settings/").status_code, 403)

    def test_staff_sees_package_group_and_fields(self):
        self.client.login(email=self.staff.email, password="testpass")
        response = self.client.get("/studio/settings/")

        self.assertContains(response, "banner_generator")
        self.assertContains(response, "BANNER_GENERATOR_FUNCTION_URL")
        self.assertContains(response, "BANNER_GENERATOR_AUTH_TOKEN")

    def test_package_values_drive_runtime_feature(self):
        package_set("BANNER_GENERATOR_FUNCTION_URL", "https://lambda.example.test/render", actor_ref="test:1584")
        package_set("BANNER_GENERATOR_AUTH_TOKEN", "token-zzz", actor_ref="test:1584")
        clear_config_cache()

        self.assertTrue(is_enabled())
        self.assertEqual(Setting.objects.get(key="BANNER_GENERATOR_FUNCTION_URL").value, "https://lambda.example.test/render")
        self.assertFalse(Setting.objects.filter(key="BANNER_GENERATOR_AUTH_TOKEN", value="token-zzz").exists())
