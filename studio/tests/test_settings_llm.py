"""Package-owned LLM settings coverage."""

from community_base.config.models import Setting
from community_base.config.service import set as package_set
from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config

User = get_user_model()
FAKE_KEY = "sk-test-package-key"


class SettingsDashboardLLMTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="llm-staff@test.com", password="testpass", is_staff=True)

    def setUp(self):
        self.client.login(email=self.staff.email, password="testpass")
        clear_config_cache()

    def tearDown(self):
        Setting.objects.filter(key__in=("LLM_API_KEY", "LLM_MODEL")).delete()
        clear_config_cache()

    def test_staff_sees_package_llm_fields(self):
        response = self.client.get("/studio/settings/")
        self.assertContains(response, "LLM_API_KEY")
        self.assertContains(response, "LLM_MODEL")

    def test_api_key_is_masked_and_stored_encrypted(self):
        package_set("LLM_API_KEY", FAKE_KEY, actor_ref="test:1584")
        response = self.client.get("/studio/settings/")
        row = Setting.objects.get(key="LLM_API_KEY")

        self.assertNotIn(FAKE_KEY, response.content.decode())
        self.assertNotEqual(row.value, FAKE_KEY)
        clear_config_cache()
        self.assertEqual(get_config("LLM_API_KEY"), FAKE_KEY)
