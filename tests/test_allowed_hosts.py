from django.test import Client, SimpleTestCase, TestCase, override_settings

from website import settings as website_settings


class AllowedHostsConfigTest(SimpleTestCase):
    def test_csv_env_uses_safe_default_when_missing(self):
        self.assertEqual(
            website_settings._csv_env(
                "ALLOWED_HOSTS",
                "localhost,127.0.0.1",
                env={},
            ),
            ["localhost", "127.0.0.1"],
        )

    def test_csv_env_strips_whitespace_and_ignores_empty_values(self):
        self.assertEqual(
            website_settings._csv_env(
                "ALLOWED_HOSTS",
                "localhost,127.0.0.1",
                env={"ALLOWED_HOSTS": " aishippinglabs.com, , www.aishippinglabs.com "},
            ),
            ["aishippinglabs.com", "www.aishippinglabs.com"],
        )


@override_settings(ALLOWED_HOSTS=["aishippinglabs.com"])
class AllowedHostsEnforcementTest(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_unknown_host_returns_400(self):
        response = self.client.get("/ping", HTTP_HOST="evil.example.com")

        self.assertEqual(response.status_code, 400)

    def test_allowed_host_reaches_health_check(self):
        response = self.client.get("/ping", HTTP_HOST="aishippinglabs.com")

        self.assertEqual(response.status_code, 200)
