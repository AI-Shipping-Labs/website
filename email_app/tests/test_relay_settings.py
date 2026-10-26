"""Production settings choose Relay; offline backends require an explicit test."""

import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


class RelaySettingsTest(SimpleTestCase):
    source = """
import json
from unittest.mock import patch
with patch('dotenv.load_dotenv'):
    import website.settings as config
print(json.dumps([config.TESTING, config.EMAIL_BACKEND, config.COMMUNITY_BASE['MAIL_BACKEND']]))
"""

    def _fresh_settings(self, *, debug, test_runner=False):
        environment = os.environ.copy()
        environment.pop("PYTEST_XDIST_WORKER", None)
        environment.update(
            DEBUG=str(debug),
            SECRET_KEY="test-only-secret-for-settings-import",
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            COMMUNITY_BASE_MAIL_BACKEND="smtp",
        )
        command = [sys.executable, "-c", self.source]
        if test_runner:
            command.append("test")
        result = subprocess.run(
            command, env=environment,
            text=True, capture_output=True, check=True, timeout=30,
        )
        return json.loads(result.stdout)

    def test_fresh_production_settings_ignore_hostile_backend_environment(self):
        self.assertEqual(self._fresh_settings(debug=False), [
            False, "email_app.backends.relay.RelayEmailBackend", "relay",
        ])

    def test_fresh_development_settings_ignore_hostile_backend_environment(self):
        self.assertEqual(self._fresh_settings(debug=True), [
            False, "email_app.backends.relay.RelayEmailBackend", "relay",
        ])

    def test_fresh_test_settings_use_locmem_and_package_relay(self):
        self.assertEqual(self._fresh_settings(debug=False, test_runner=True), [
            True, "django.core.mail.backends.locmem.EmailBackend", "relay",
        ])
