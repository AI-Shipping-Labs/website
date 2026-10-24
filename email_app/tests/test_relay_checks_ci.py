"""CI must enforce the same production Relay configuration checks."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from email_app.checks import check_relay_configuration_in_production


@override_settings(TESTING=False, DEBUG=False, COMMUNITY_BASE={"MAIL_BACKEND": "relay"})
class RelayConfigurationCITest(SimpleTestCase):
    def configuration_errors(self, base_url, api_key):
        values = {"RELAY_BASE_URL": base_url, "RELAY_API_KEY": api_key}
        with (
            patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
            patch("email_app.checks.get_config", side_effect=lambda key, default=None: values.get(key, default)),
        ):
            return check_relay_configuration_in_production(None)

    def test_missing_api_key_is_reported_in_github_actions(self):
        errors = self.configuration_errors("https://relay.example.test", "")

        self.assertEqual([error.id for error in errors], ["email_app.E002"])
        self.assertIn("RELAY_API_KEY", errors[0].msg)

    def test_missing_base_url_is_reported_in_github_actions(self):
        errors = self.configuration_errors("", "test-client-key")

        self.assertEqual([error.id for error in errors], ["email_app.E002"])
        self.assertIn("RELAY_BASE_URL", errors[0].msg)
