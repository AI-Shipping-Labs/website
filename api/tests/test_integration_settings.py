"""Tests for the write-only integration settings API (issue #633).

The endpoint is ``POST /api/integrations/settings``. It mutates
``IntegrationSetting`` rows for keys in
``integrations.settings_registry.INTEGRATION_GROUPS`` and is gated by a
staff-scoped ``Authorization: Token <key>`` header.

These tests assert four contracts that must hold simultaneously:

1. Auth — missing / non-staff / unknown token returns 401.
2. Method — anything other than POST returns 405.
3. Allowlist — keys outside the registry are rejected all-or-nothing.
4. No echo — the response NEVER contains key names, stored values, the
   previous value, the request body, or the literal substring "value".
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from integrations.models import IntegrationSetting

User = get_user_model()

URL = "/api/integrations/settings"


class IntegrationSettingsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-integ-api@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-integ-api@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="integ")
        # Non-staff token is created without going through the staff
        # check (mirrors test_sync_sources.py setup).
        cls.non_staff_token = Token(
            key="non-staff-integ-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    # ---- helpers ----------------------------------------------------------

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post_json(self, payload, token=None):
        headers = self._auth(token)
        return self.client.post(
            URL,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def _assert_no_echo(self, response, *forbidden_substrings):
        """Every error and success response from this endpoint must not
        leak request values, stored values, key names, or the literal
        substring ``"value"``. Assert on the raw body string so we catch
        leaks regardless of where in the JSON tree they appear.
        """
        body = response.content.decode("utf-8")
        # The literal substring "value" must never appear in any response
        # — neither as a JSON key nor inside an echo.
        self.assertNotIn("value", body)
        for forbidden in forbidden_substrings:
            self.assertNotIn(forbidden, body)

    # ---- auth -------------------------------------------------------------

    def test_post_requires_authorization_header(self):
        response = self.client.post(
            URL,
            data=json.dumps({"updates": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )

    def test_post_rejects_non_staff_token(self):
        response = self._post_json({"updates": []}, token=self.non_staff_token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_post_rejects_invalid_token(self):
        response = self.client.post(
            URL,
            data=json.dumps({"updates": []}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Token does-not-exist",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    # ---- method gating ----------------------------------------------------

    def test_get_returns_405_method_not_allowed(self):
        response = self.client.get(URL, **self._auth())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})
        # 405 must not leak anything either.
        self._assert_no_echo(response)

    def test_delete_returns_405_method_not_allowed(self):
        response = self.client.delete(URL, **self._auth())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})
        self._assert_no_echo(response)

    def test_put_and_patch_return_405(self):
        for method in ("put", "patch"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    URL,
                    data=json.dumps({"updates": []}),
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(
                    response.json(),
                    {"error": "Method not allowed"},
                )

    # ---- happy path -------------------------------------------------------

    def test_post_writes_allowed_registry_key(self):
        payload = {
            "updates": [
                {"key": "CONTENT_CDN_BASE", "value": "https://cdn.example.com"},
            ],
        }
        response = self._post_json(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "updated": 1},
        )
        row = IntegrationSetting.objects.get(key="CONTENT_CDN_BASE")
        self.assertEqual(row.value, "https://cdn.example.com")
        self.assertEqual(row.group, "s3_content")

    def test_post_response_does_not_echo_key_or_value(self):
        # First write — fresh key, fresh value.
        first_value = "https://cdn.example.com"
        response = self._post_json({
            "updates": [{"key": "CONTENT_CDN_BASE", "value": first_value}],
        })
        self.assertEqual(response.status_code, 200)
        self._assert_no_echo(response, "CONTENT_CDN_BASE", first_value)

        # Overwrite the same key with a NEW value. The response must not
        # leak the previous value, the new value, or the key name.
        second_value = "https://new-cdn.example.org"
        response = self._post_json({
            "updates": [{"key": "CONTENT_CDN_BASE", "value": second_value}],
        })
        self.assertEqual(response.status_code, 200)
        self._assert_no_echo(
            response,
            "CONTENT_CDN_BASE",
            first_value,
            second_value,
        )
        # And the row really did get updated.
        self.assertEqual(
            IntegrationSetting.objects.get(key="CONTENT_CDN_BASE").value,
            second_value,
        )

    # ---- allowlist --------------------------------------------------------

    def test_post_rejects_unknown_key_and_writes_nothing(self):
        starting_count = IntegrationSetting.objects.count()
        response = self._post_json({
            "updates": [{"key": "DJANGO_SECRET_KEY", "value": "x"}],
        })

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "invalid_key")
        self.assertEqual(
            body["details"]["invalid_keys"],
            ["DJANGO_SECRET_KEY"],
        )
        self.assertEqual(IntegrationSetting.objects.count(), starting_count)

    def test_post_with_mixed_valid_and_invalid_keys_writes_nothing(self):
        starting_count = IntegrationSetting.objects.count()
        response = self._post_json({
            "updates": [
                {"key": "CONTENT_CDN_BASE", "value": "https://cdn.example.com"},
                {"key": "DATABASE_URL", "value": "postgres://hacker"},
            ],
        })

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "invalid_key")
        self.assertIn("DATABASE_URL", body["details"]["invalid_keys"])
        # All-or-nothing: the valid key did NOT get written.
        self.assertFalse(
            IntegrationSetting.objects.filter(key="CONTENT_CDN_BASE").exists()
        )
        self.assertEqual(IntegrationSetting.objects.count(), starting_count)

    # ---- malformed bodies -------------------------------------------------

    def test_post_rejects_invalid_json_body(self):
        response = self.client.post(
            URL,
            data=b"not json",
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_json")

    def test_post_rejects_non_object_body(self):
        for body in ([], "hello", 42):
            with self.subTest(body=body):
                response = self.client.post(
                    URL,
                    data=json.dumps(body),
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_type")

    # ---- empty-string clears row (Studio parity) --------------------------

    def test_post_empty_value_clears_existing_row(self):
        IntegrationSetting.objects.create(
            key="CONTENT_CDN_BASE",
            value="https://old-cdn.example.com",
            is_secret=False,
            group="s3_content",
            description="",
        )
        response = self._post_json({
            "updates": [{"key": "CONTENT_CDN_BASE", "value": ""}],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "updated": 1})
        self.assertFalse(
            IntegrationSetting.objects.filter(key="CONTENT_CDN_BASE").exists()
        )
        self._assert_no_echo(
            response,
            "CONTENT_CDN_BASE",
            "https://old-cdn.example.com",
        )

    # ---- booleans ---------------------------------------------------------

    def test_post_boolean_key_accepts_bool_and_string(self):
        # JSON true literal
        response = self._post_json({
            "updates": [{"key": "SLACK_ENABLED", "value": True}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            IntegrationSetting.objects.get(key="SLACK_ENABLED").value,
            "true",
        )

        # String "true" — must persist as the same "true" literal.
        IntegrationSetting.objects.filter(key="SLACK_ENABLED").delete()
        response = self._post_json({
            "updates": [{"key": "SLACK_ENABLED", "value": "true"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            IntegrationSetting.objects.get(key="SLACK_ENABLED").value,
            "true",
        )

        # JSON false literal — stored as "false", not absent.
        response = self._post_json({
            "updates": [{"key": "SLACK_ENABLED", "value": False}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            IntegrationSetting.objects.get(key="SLACK_ENABLED").value,
            "false",
        )

    # ---- cache invalidation ----------------------------------------------

    def test_post_calls_clear_config_cache_once(self):
        # Patch the symbol where it is USED, not where it is defined —
        # the view imports it into its own module namespace.
        with patch(
            "api.views.integration_settings.clear_config_cache"
        ) as mock_clear:
            response = self._post_json({
                "updates": [
                    {"key": "CONTENT_CDN_BASE", "value": "https://cdn.example.com"},
                    {"key": "SLACK_ENABLED", "value": True},
                    {"key": "SITE_BASE_URL", "value": "https://example.com"},
                ],
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "updated": 3})
        self.assertEqual(mock_clear.call_count, 1)

    # ---- error-path leak check -------------------------------------------

    def test_post_invalid_key_response_does_not_echo_value(self):
        secret_value = "supersecretdoNOTleak"
        response = self._post_json({
            "updates": [{"key": "DJANGO_SECRET_KEY", "value": secret_value}],
        })

        self.assertEqual(response.status_code, 400)
        body_str = response.content.decode("utf-8")
        # The offending value MUST NOT appear in the response anywhere.
        self.assertNotIn(secret_value, body_str)
        # And the literal substring "value" must not appear either.
        self.assertNotIn("value", body_str)
        # The key name IS allowed to appear (in details.invalid_keys) —
        # that's the only way the caller knows what to fix.
        self.assertIn("DJANGO_SECRET_KEY", body_str)
