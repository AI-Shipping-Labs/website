"""Documentation links in the package-owned Studio settings page."""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class IntegrationDocsHelpIconRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email="docs-admin@test.com", password="testpass", is_staff=True
        )

    def setUp(self):
        self.client.login(email=self.staff_user.email, password="testpass")

    def test_registered_setting_has_its_documentation_link(self):
        response = self.client.get("/studio/settings/")

        self.assertContains(
            response,
            'href="https://github.com/AI-Shipping-Labs/website/blob/main/_docs/integrations/stripe.md#stripe_webhook_secret"',
        )
        self.assertNotContains(response, 'href="_docs/integrations/stripe.md#stripe_webhook_secret"')

    def test_observability_settings_keep_restart_feedback(self):
        response = self.client.get("/studio/settings/")

        self.assertContains(response, 'data-settings-restart-hint')
        self.assertContains(response, 'data-settings-restart="LOGFIRE_TOKEN"')

    def test_internal_docs_route_is_removed(self):
        response = self.client.get("/studio/docs/integrations/stripe")
        self.assertEqual(response.status_code, 404)
