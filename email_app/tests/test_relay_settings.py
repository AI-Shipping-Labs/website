"""Production settings choose Relay; offline backends require an explicit test."""

import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


class RelaySettingsTest(SimpleTestCase):
    def test_fresh_production_settings_choose_relay_for_both_backends(self):
        environment = os.environ.copy()
        for key in ("EMAIL_BACKEND", "COMMUNITY_BASE_MAIL_BACKEND", "PYTEST_XDIST_WORKER"):
            environment.pop(key, None)
        environment.update(DEBUG="False", SECRET_KEY="test-only-secret-for-settings-import")
        source = """
import json
from unittest.mock import patch
with patch('dotenv.load_dotenv'):
    import website.settings as config
print(json.dumps([config.TESTING, config.EMAIL_BACKEND, config.COMMUNITY_BASE['MAIL_BACKEND']]))
"""
        result = subprocess.run(
            [sys.executable, "-c", source], env=environment,
            text=True, capture_output=True, check=True, timeout=30,
        )
        self.assertEqual(json.loads(result.stdout), [
            False, "email_app.backends.relay.RelayEmailBackend", "relay",
        ])
