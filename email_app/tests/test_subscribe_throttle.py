"""Newsletter subscribe throttle (issue #1516)."""

import json
from unittest.mock import patch

from django.test import TestCase, tag

from accounts.models import User
from accounts.services.auth_throttle import (
    MAIL_EMAIL_LIMIT_KEY,
    MAIL_IP_LIMIT_KEY,
    MAIL_WINDOW_KEY,
    THROTTLED_ERROR,
    clear_auth_throttle_cache,
)
from integrations.config import clear_config_cache, reset_local_config_cache
from integrations.models import IntegrationSetting

THROTTLED_JSON = {"error": THROTTLED_ERROR}
SUBSCRIBE_OK_SNIPPET = "free account"


def _set_mail_limits(ip_limit, email_limit, window=3600):
    for key, value in (
        (MAIL_IP_LIMIT_KEY, ip_limit),
        (MAIL_EMAIL_LIMIT_KEY, email_limit),
        (MAIL_WINDOW_KEY, window),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={"value": str(value)},
        )
    clear_config_cache()
    clear_auth_throttle_cache()


@tag("core")
class SubscribeAuthThrottleTest(TestCase):
    url = "/api/subscribe"

    def setUp(self):
        reset_local_config_cache()
        clear_auth_throttle_cache()

    def tearDown(self):
        IntegrationSetting.objects.filter(
            key__in=(MAIL_IP_LIMIT_KEY, MAIL_EMAIL_LIMIT_KEY, MAIL_WINDOW_KEY)
        ).delete()
        reset_local_config_cache()
        clear_auth_throttle_cache()

    def _post(self, payload, **extra):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_ip_limit_does_not_create_user_or_send_mail(
        self, mock_send
    ):
        _set_mail_limits(ip_limit=1, email_limit=3)
        first = self._post(
            {"email": "one@example.com"},
            REMOTE_ADDR="203.0.113.70",
        )
        self.assertEqual(first.json()["status"], "ok")
        self.assertIn(SUBSCRIBE_OK_SNIPPET, first.json()["message"].lower())
        mock_send.reset_mock()
        blocked = self._post(
            {"email": "two@example.com"},
            REMOTE_ADDR="203.0.113.70",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json(), THROTTLED_JSON)
        mock_send.assert_not_called()
        self.assertFalse(User.objects.filter(email="two@example.com").exists())

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_email_limit_returns_429_not_generic_200(self, mock_send):
        _set_mail_limits(ip_limit=8, email_limit=1)
        first = self._post({"email": "repeat@example.com"})
        self.assertEqual(first.json()["status"], "ok")
        self.assertIn(SUBSCRIBE_OK_SNIPPET, first.json()["message"].lower())
        blocked = self._post(
            {"email": "repeat@example.com"},
            REMOTE_ADDR="198.51.100.44",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json(), THROTTLED_JSON)
        self.assertNotIn("account", json.dumps(blocked.json()).lower())
        self.assertEqual(mock_send.call_count, 1)

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_subscribe_under_limit_keeps_generic_200(self, mock_send):
        _set_mail_limits(ip_limit=8, email_limit=3)
        response = self._post({"email": "ok@example.com"})
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("account", response.json()["message"].lower())
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(mock_send.call_args[0][0].email, "ok@example.com")

    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_invalid_email_and_json_do_not_consume_bucket(self, mock_send):
        _set_mail_limits(ip_limit=1, email_limit=1)
        invalid_email = self._post({"email": "not-an-email"})
        self.assertEqual(invalid_email.status_code, 400)
        missing = self._post({"email": ""})
        self.assertEqual(missing.status_code, 400)
        invalid_json = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(invalid_json.status_code, 400)
        ok = self._post({"email": "first-valid@example.com"})
        self.assertEqual(ok.json()["status"], "ok")
        self.assertIn(SUBSCRIBE_OK_SNIPPET, ok.json()["message"].lower())
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(
            mock_send.call_args[0][0].email, "first-valid@example.com"
        )
