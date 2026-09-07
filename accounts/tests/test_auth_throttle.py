"""Shared public-auth throttle (issue #1516).

Limits are lowered via IntegrationSetting so tests never loop against
the production defaults of 20 / 10 / 8 / 3.
"""

import json
import time
from unittest.mock import patch

from django.core.cache import cache, caches
from django.test import RequestFactory, TestCase, override_settings, tag

from accounts.models import User
from accounts.services.auth_throttle import (
    CACHE_ALIAS,
    DEFAULT_LOGIN_EMAIL_LIMIT,
    DEFAULT_LOGIN_IP_LIMIT,
    DEFAULT_LOGIN_WINDOW_SECONDS,
    DEFAULT_MAIL_EMAIL_LIMIT,
    DEFAULT_MAIL_IP_LIMIT,
    DEFAULT_MAIL_WINDOW_SECONDS,
    LOGIN_EMAIL_LIMIT_KEY,
    LOGIN_IP_LIMIT_KEY,
    LOGIN_WINDOW_KEY,
    MAIL_EMAIL_LIMIT_KEY,
    MAIL_IP_LIMIT_KEY,
    MAIL_WINDOW_KEY,
    SCOPE_LOGIN,
    SCOPE_REGISTER,
    SCOPE_SUBSCRIBE,
    THROTTLED_ERROR,
    auth_throttle_cache_key,
    clear_auth_throttle_cache,
    consume_auth_throttle,
    hash_throttle_value,
    normalize_throttle_email,
    resolve_auth_throttle_limits,
    resolve_positive_int,
)
from integrations.config import clear_config_cache, reset_local_config_cache
from integrations.models import IntegrationSetting

FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
THROTTLED_JSON = {"error": THROTTLED_ERROR}
INVALID_LOGIN = "Invalid email or password"
RESET_OK_MESSAGE = (
    "If an account exists for that email, we’ll send password reset "
    "instructions shortly."
)


def _set_limits(**values):
    for key, value in values.items():
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={"value": str(value)},
        )
    clear_config_cache()
    clear_auth_throttle_cache()


class AuthThrottleTestMixin:
    def setUp(self):
        super().setUp()
        reset_local_config_cache()
        clear_auth_throttle_cache()
        cache.clear()

    def tearDown(self):
        IntegrationSetting.objects.filter(
            key__in=(
                LOGIN_IP_LIMIT_KEY,
                LOGIN_EMAIL_LIMIT_KEY,
                LOGIN_WINDOW_KEY,
                MAIL_IP_LIMIT_KEY,
                MAIL_EMAIL_LIMIT_KEY,
                MAIL_WINDOW_KEY,
            )
        ).delete()
        reset_local_config_cache()
        clear_auth_throttle_cache()
        super().tearDown()

    def _post_json(self, url, payload, **extra):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )


@tag("core")
class AuthThrottleHelperTest(AuthThrottleTestMixin, TestCase):
    def test_non_numeric_and_non_positive_values_fall_back_to_defaults(self):
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: "abc",
                LOGIN_EMAIL_LIMIT_KEY: "0",
                LOGIN_WINDOW_KEY: "-5",
                MAIL_IP_LIMIT_KEY: "",
                MAIL_EMAIL_LIMIT_KEY: "nope",
                MAIL_WINDOW_KEY: "0",
            }
        )
        self.assertEqual(
            resolve_positive_int(LOGIN_IP_LIMIT_KEY, DEFAULT_LOGIN_IP_LIMIT),
            DEFAULT_LOGIN_IP_LIMIT,
        )
        self.assertEqual(
            resolve_positive_int(LOGIN_EMAIL_LIMIT_KEY, DEFAULT_LOGIN_EMAIL_LIMIT),
            DEFAULT_LOGIN_EMAIL_LIMIT,
        )
        self.assertEqual(
            resolve_positive_int(LOGIN_WINDOW_KEY, DEFAULT_LOGIN_WINDOW_SECONDS),
            DEFAULT_LOGIN_WINDOW_SECONDS,
        )
        self.assertEqual(
            resolve_auth_throttle_limits(SCOPE_LOGIN),
            (
                DEFAULT_LOGIN_IP_LIMIT,
                DEFAULT_LOGIN_EMAIL_LIMIT,
                DEFAULT_LOGIN_WINDOW_SECONDS,
            ),
        )
        self.assertEqual(
            resolve_auth_throttle_limits(SCOPE_REGISTER),
            (
                DEFAULT_MAIL_IP_LIMIT,
                DEFAULT_MAIL_EMAIL_LIMIT,
                DEFAULT_MAIL_WINDOW_SECONDS,
            ),
        )

    def test_studio_override_is_honored_after_config_cache_invalidation(self):
        factory = RequestFactory()
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 10,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        first = consume_auth_throttle(
            factory.post("/", REMOTE_ADDR="203.0.113.10"),
            "one@example.com",
            SCOPE_LOGIN,
        )
        self.assertIsNone(first)
        blocked = consume_auth_throttle(
            factory.post("/", REMOTE_ADDR="203.0.113.10"),
            "two@example.com",
            SCOPE_LOGIN,
        )
        self.assertEqual(blocked.status_code, 429)

        IntegrationSetting.objects.update_or_create(
            key=LOGIN_IP_LIMIT_KEY,
            defaults={"value": "5"},
        )
        clear_config_cache()
        allowed = consume_auth_throttle(
            factory.post("/", REMOTE_ADDR="203.0.113.10"),
            "three@example.com",
            SCOPE_LOGIN,
        )
        self.assertIsNone(allowed)

    def test_counters_use_django_q_cache_and_hashed_keys(self):
        factory = RequestFactory()
        email = "Hash.Me@Example.COM"
        ip = "198.51.100.20"
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 5,
                LOGIN_EMAIL_LIMIT_KEY: 5,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        consume_auth_throttle(
            factory.post("/", REMOTE_ADDR=ip),
            email,
            SCOPE_LOGIN,
        )
        normalized = normalize_throttle_email(email)
        ip_key = auth_throttle_cache_key(
            SCOPE_LOGIN, "ip", hash_throttle_value(ip)
        )
        email_key = auth_throttle_cache_key(
            SCOPE_LOGIN, "email", hash_throttle_value(normalized)
        )
        shared = caches[CACHE_ALIAS]
        self.assertEqual(shared.get(ip_key), 1)
        self.assertEqual(shared.get(email_key), 1)
        self.assertIsNone(cache.get(ip_key))
        self.assertIsNone(cache.get(email_key))
        stored = " ".join(str(key) for key in getattr(shared, "_cache", {}))
        self.assertNotIn(ip, stored)
        self.assertNotIn(email, stored)
        self.assertNotIn(normalized, stored)
        self.assertIn(hash_throttle_value(ip), stored)
        self.assertIn(hash_throttle_value(normalized), stored)

    def test_empty_ip_uses_unknown_digest(self):
        factory = RequestFactory()
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 10,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        request = factory.post("/", REMOTE_ADDR="")
        self.assertIsNone(
            consume_auth_throttle(request, "a@example.com", SCOPE_LOGIN)
        )
        blocked = consume_auth_throttle(
            factory.post("/", REMOTE_ADDR="not-an-ip"),
            "b@example.com",
            SCOPE_LOGIN,
        )
        self.assertEqual(blocked.status_code, 429)
        unknown_key = auth_throttle_cache_key(
            SCOPE_LOGIN, "ip", hash_throttle_value("unknown")
        )
        self.assertEqual(caches[CACHE_ALIAS].get(unknown_key), 2)

    def test_ip_over_limit_does_not_increment_email_bucket(self):
        factory = RequestFactory()
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 10,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        consume_auth_throttle(
            factory.post("/", REMOTE_ADDR="203.0.113.1"),
            "kept@example.com",
            SCOPE_LOGIN,
        )
        blocked = consume_auth_throttle(
            factory.post("/", REMOTE_ADDR="203.0.113.1"),
            "other@example.com",
            SCOPE_LOGIN,
        )
        self.assertEqual(blocked.status_code, 429)
        email_key = auth_throttle_cache_key(
            SCOPE_LOGIN,
            "email",
            hash_throttle_value(normalize_throttle_email("other@example.com")),
        )
        self.assertIsNone(caches[CACHE_ALIAS].get(email_key))

    def test_scopes_do_not_share_counters(self):
        factory = RequestFactory()
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 1,
                LOGIN_WINDOW_KEY: 900,
                MAIL_IP_LIMIT_KEY: 8,
                MAIL_EMAIL_LIMIT_KEY: 3,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        request = factory.post("/", REMOTE_ADDR="203.0.113.9")
        self.assertIsNone(
            consume_auth_throttle(request, "same@example.com", SCOPE_LOGIN)
        )
        self.assertEqual(
            consume_auth_throttle(
                request, "same@example.com", SCOPE_LOGIN
            ).status_code,
            429,
        )
        self.assertIsNone(
            consume_auth_throttle(request, "same@example.com", SCOPE_SUBSCRIBE)
        )

    def test_window_expiry_allows_the_next_request(self):
        factory = RequestFactory()
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 1,
                LOGIN_WINDOW_KEY: 1,
            }
        )
        request = factory.post("/", REMOTE_ADDR="203.0.113.8")
        self.assertIsNone(
            consume_auth_throttle(request, "exp@example.com", SCOPE_LOGIN)
        )
        blocked = consume_auth_throttle(
            request, "exp@example.com", SCOPE_LOGIN
        )
        self.assertEqual(blocked.status_code, 429)
        time.sleep(1.1)
        self.assertIsNone(
            consume_auth_throttle(request, "exp@example.com", SCOPE_LOGIN)
        )

    def test_throttled_response_shape_and_log_have_no_raw_identity(self):
        factory = RequestFactory()
        email = "secret.user@example.com"
        ip = "203.0.113.77"
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 1,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        consume_auth_throttle(
            factory.post("/", REMOTE_ADDR=ip),
            email,
            SCOPE_LOGIN,
        )
        with self.assertLogs(
            "accounts.services.auth_throttle", level="WARNING"
        ) as captured:
            response = consume_auth_throttle(
                factory.post("/", REMOTE_ADDR=ip),
                email,
                SCOPE_LOGIN,
            )
        self.assertEqual(response.status_code, 429)
        payload = json.loads(response.content)
        self.assertEqual(payload, THROTTLED_JSON)
        self.assertIn("Retry-After", response)
        retry_after = int(response["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 900)
        record = captured.records[0]
        self.assertEqual(record.throttle_scope, SCOPE_LOGIN)
        self.assertEqual(record.throttle_bucket, "ip")
        message = record.getMessage()
        self.assertNotIn(email, message)
        self.assertNotIn(ip, message)
        self.assertNotIn(email, json.dumps(payload))


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class LoginAuthThrottleTest(AuthThrottleTestMixin, TestCase):
    url = "/api/login"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="login@example.com", password="correct1234"
        )

    def test_login_ip_limit_returns_429_without_password_work(self):
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 2,
                LOGIN_EMAIL_LIMIT_KEY: 10,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        for _ in range(2):
            resp = self._post_json(
                self.url,
                {"email": "login@example.com", "password": "wrongpass"},
                REMOTE_ADDR="203.0.113.40",
            )
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(resp.json()["error"], INVALID_LOGIN)

        with (
            patch("accounts.views.auth.resolve_user_by_email") as resolve,
            patch.object(User, "check_password") as check_password,
            patch.object(User, "set_password") as set_password,
        ):
            blocked = self._post_json(
                self.url,
                {"email": "login@example.com", "password": "correct1234"},
                REMOTE_ADDR="203.0.113.40",
            )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json(), THROTTLED_JSON)
        self.assertIn("Retry-After", blocked)
        resolve.assert_not_called()
        check_password.assert_not_called()
        set_password.assert_not_called()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_email_limit_applies_across_ips_for_known_and_unknown(self):
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 20,
                LOGIN_EMAIL_LIMIT_KEY: 2,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        for index, email in enumerate(
            ("login@example.com", "nobody@example.com")
        ):
            with self.subTest(email=email):
                clear_auth_throttle_cache()
                for attempt in range(2):
                    resp = self._post_json(
                        self.url,
                        {"email": email, "password": "wrongpass"},
                        REMOTE_ADDR=f"198.51.100.{index * 10 + attempt + 1}",
                    )
                    self.assertEqual(resp.status_code, 401)
                    self.assertEqual(resp.json()["error"], INVALID_LOGIN)
                blocked = self._post_json(
                    self.url,
                    {"email": email, "password": "wrongpass"},
                    REMOTE_ADDR=f"198.51.100.{index * 10 + 9}",
                )
                self.assertEqual(blocked.status_code, 429)
                self.assertEqual(blocked.json(), THROTTLED_JSON)
                self.assertNotIn("exists", json.dumps(blocked.json()).lower())

    def test_successful_login_under_limit_establishes_session(self):
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 2,
                LOGIN_EMAIL_LIMIT_KEY: 2,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        resp = self._post_json(
            self.url,
            {"email": "login@example.com", "password": "correct1234"},
        )
        self.assertEqual(resp.json()["status"], "ok")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_missing_fields_and_invalid_json_do_not_consume_bucket(self):
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 1,
                LOGIN_WINDOW_KEY: 900,
            }
        )
        missing = self._post_json(self.url, {"email": "login@example.com"})
        self.assertEqual(missing.status_code, 400)
        invalid = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(invalid.status_code, 400)
        resp = self._post_json(
            self.url,
            {"email": "login@example.com", "password": "correct1234"},
        )
        self.assertEqual(resp.json()["status"], "ok")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class RegisterAuthThrottleTest(AuthThrottleTestMixin, TestCase):
    url = "/api/register"

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_ip_limit_does_not_create_user_or_send_mail(
        self, mock_probe, mock_send
    ):
        _set_limits(
            **{
                MAIL_IP_LIMIT_KEY: 1,
                MAIL_EMAIL_LIMIT_KEY: 3,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        first = self._post_json(
            self.url,
            {"email": "one@example.com", "password": "secure1234"},
            REMOTE_ADDR="203.0.113.50",
        )
        self.assertEqual(first.status_code, 201)
        self.client.logout()
        mock_send.reset_mock()
        mock_probe.reset_mock()
        with patch(
            "accounts.views.auth.validate_password"
        ) as validate_password:
            blocked = self._post_json(
                self.url,
                {"email": "two@example.com", "password": "secure1234"},
                REMOTE_ADDR="203.0.113.50",
            )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json(), THROTTLED_JSON)
        validate_password.assert_not_called()
        mock_send.assert_not_called()
        mock_probe.assert_not_called()
        self.assertFalse(User.objects.filter(email="two@example.com").exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("accounts.views.auth._send_verification_email")
    @patch("accounts.views.auth._probe_slack_membership_on_signup")
    def test_register_email_limit_allows_a_different_email_from_same_ip(
        self, _probe, _send
    ):
        _set_limits(
            **{
                MAIL_IP_LIMIT_KEY: 8,
                MAIL_EMAIL_LIMIT_KEY: 1,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        first = self._post_json(
            self.url,
            {"email": "same@example.com", "password": "secure1234"},
            REMOTE_ADDR="203.0.113.51",
        )
        self.assertEqual(first.status_code, 201)
        self.client.logout()
        blocked = self._post_json(
            self.url,
            {"email": "same@example.com", "password": "secure1234"},
            REMOTE_ADDR="198.51.100.2",
        )
        self.assertEqual(blocked.status_code, 429)
        other = self._post_json(
            self.url,
            {"email": "other@example.com", "password": "secure1234"},
            REMOTE_ADDR="203.0.113.51",
        )
        self.assertEqual(other.status_code, 201)
        self.assertTrue(User.objects.filter(email="other@example.com").exists())

    def test_register_missing_fields_do_not_consume_bucket(self):
        _set_limits(
            **{
                MAIL_IP_LIMIT_KEY: 1,
                MAIL_EMAIL_LIMIT_KEY: 1,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        missing = self._post_json(self.url, {"email": "skip@example.com"})
        self.assertEqual(missing.status_code, 400)
        with (
            patch("accounts.views.auth._send_verification_email"),
            patch("accounts.views.auth._probe_slack_membership_on_signup"),
        ):
            created = self._post_json(
                self.url,
                {"email": "skip@example.com", "password": "secure1234"},
            )
        self.assertEqual(created.status_code, 201)


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PasswordResetRequestAuthThrottleTest(AuthThrottleTestMixin, TestCase):
    url = "/api/password-reset-request"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="reset@example.com", password="correct1234"
        )

    @patch("accounts.views.auth._send_password_reset_email")
    def test_reset_request_mail_limit_skips_send_for_known_and_unknown(
        self, mock_send
    ):
        _set_limits(
            **{
                MAIL_IP_LIMIT_KEY: 8,
                MAIL_EMAIL_LIMIT_KEY: 1,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        for email in ("reset@example.com", "nobody@example.com"):
            with self.subTest(email=email):
                clear_auth_throttle_cache()
                mock_send.reset_mock()
                first = self._post_json(self.url, {"email": email})
                self.assertEqual(first.json()["status"], "ok")
                self.assertEqual(first.json()["message"], RESET_OK_MESSAGE)
                blocked = self._post_json(self.url, {"email": email})
                self.assertEqual(blocked.status_code, 429)
                self.assertEqual(blocked.json(), THROTTLED_JSON)
                if email == "reset@example.com":
                    mock_send.assert_called_once_with(self.user)
                else:
                    mock_send.assert_not_called()

    @patch("accounts.views.auth._send_password_reset_email")
    def test_reset_request_missing_fields_do_not_consume_bucket(self, mock_send):
        _set_limits(
            **{
                MAIL_IP_LIMIT_KEY: 1,
                MAIL_EMAIL_LIMIT_KEY: 1,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        missing = self._post_json(self.url, {})
        self.assertEqual(missing.status_code, 400)
        invalid = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(invalid.status_code, 400)
        ok = self._post_json(self.url, {"email": "reset@example.com"})
        self.assertEqual(ok.json()["status"], "ok")
        self.assertEqual(ok.json()["message"], RESET_OK_MESSAGE)
        mock_send.assert_called_once_with(self.user)


@tag("core")
class AuthThrottleScopeIsolationViewTest(AuthThrottleTestMixin, TestCase):
    @patch("email_app.views.newsletter._send_subscribe_verification_email")
    def test_exhausted_login_does_not_block_first_subscribe(self, mock_send):
        _set_limits(
            **{
                LOGIN_IP_LIMIT_KEY: 1,
                LOGIN_EMAIL_LIMIT_KEY: 1,
                LOGIN_WINDOW_KEY: 900,
                MAIL_IP_LIMIT_KEY: 8,
                MAIL_EMAIL_LIMIT_KEY: 3,
                MAIL_WINDOW_KEY: 3600,
            }
        )
        self._post_json(
            "/api/login",
            {"email": "ghost@example.com", "password": "wrongpass"},
            REMOTE_ADDR="203.0.113.60",
        )
        blocked = self._post_json(
            "/api/login",
            {"email": "ghost@example.com", "password": "wrongpass"},
            REMOTE_ADDR="203.0.113.60",
        )
        self.assertEqual(blocked.status_code, 429)
        subscribe = self._post_json(
            "/api/subscribe",
            {"email": "fresh-sub@example.com"},
            REMOTE_ADDR="203.0.113.60",
        )
        self.assertEqual(subscribe.json()["status"], "ok")
        self.assertIn("account", subscribe.json()["message"].lower())
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(mock_send.call_args[0][0].email, "fresh-sub@example.com")
        self.assertTrue(
            User.objects.filter(email="fresh-sub@example.com").exists()
        )
