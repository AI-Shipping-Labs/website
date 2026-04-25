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

    def test_unknown_host_is_rejected_on_user_facing_routes(self):
        # Any non-/ping path goes through CommonMiddleware → get_host()
        # → ALLOWED_HOSTS validation → 400 on a disallowed host.
        response = self.client.get("/", HTTP_HOST="evil.example.com")

        self.assertEqual(response.status_code, 400)

    def test_allowed_host_reaches_health_check(self):
        response = self.client.get("/ping", HTTP_HOST="aishippinglabs.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_health_check_bypasses_host_validation(self):
        # ALB health checks hit the container by its VPC IP, so the Host
        # header is e.g. "10.0.1.189:8000" — not in ALLOWED_HOSTS. The
        # HealthCheckMiddleware must short-circuit /ping with 200 before
        # CommonMiddleware calls get_host().
        response = self.client.get("/ping", HTTP_HOST="10.0.1.189:8000")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_health_check_bypass_does_not_leak_to_other_paths(self):
        # Sanity: the /ping bypass must be path-exact, not a prefix match.
        response = self.client.get("/pingX", HTTP_HOST="10.0.1.189:8000")

        self.assertEqual(response.status_code, 400)
