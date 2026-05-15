"""Tests for the integration settings API (issues #633, #640).

The endpoint is ``/api/integrations/settings``. POST (#633) mutates
``IntegrationSetting`` rows for keys in
``integrations.settings_registry.INTEGRATION_GROUPS``. GET (#640) lists
every registered key with metadata and a ``source`` enum but NEVER the
actual value. Both methods are gated by a staff-scoped
``Authorization: Token <key>`` header.

These tests assert four contracts that must hold simultaneously:

1. Auth — missing / non-staff / unknown token returns 401.
2. Method — anything other than GET / POST returns 405.
3. Allowlist — keys outside the registry are rejected all-or-nothing.
4. No echo — neither write responses nor read responses contain the
   actual value of any setting (verified by string assertion against a
   planted sentinel and by asserting the substring ``"value"`` never
   appears).
"""

import json
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Token
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS

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


class IntegrationSettingsGetApiTest(TestCase):
    """GET /api/integrations/settings — list keys + source, no values (#640).

    Separate class from the POST suite because the GET path needs
    different fixtures (env / settings overrides) and asserting on the
    same staff token from a different test class keeps the two surfaces
    independently runnable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-integ-api-get@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-integ-api-get@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="integ-get")
        cls.non_staff_token = Token(
            key="non-staff-integ-get-token",
            user=cls.member,
            name="legacy-member-token-get",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    # ---- helpers ----------------------------------------------------------

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _get(self, token=None):
        return self.client.get(URL, **self._auth(token))

    def _entry_for(self, body, key):
        for entry in body["settings"]:
            if entry["key"] == key:
                return entry
        raise AssertionError(f"key {key!r} missing from GET response")

    # ---- shape & ordering -------------------------------------------------

    def test_get_lists_all_registry_keys_in_order(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertIn("settings", body)
        entries = body["settings"]

        expected_order = [
            key_def["key"]
            for group in INTEGRATION_GROUPS
            for key_def in group["keys"]
        ]
        actual_order = [entry["key"] for entry in entries]
        self.assertEqual(actual_order, expected_order)

        # Each entry has the documented shape — and crucially no "value"
        # field. We check on a representative entry (Stripe secret) so
        # the assertion fails the day someone adds a `value` field by
        # accident.
        sample = self._entry_for(body, "STRIPE_SECRET_KEY")
        self.assertEqual(
            set(sample.keys()),
            {
                "key",
                "group",
                "label",
                "description",
                "is_secret",
                "is_boolean",
                "configured",
                "source",
                "docs_url",
            },
        )
        self.assertEqual(sample["group"], "stripe")
        self.assertEqual(sample["label"], "Stripe")
        self.assertTrue(sample["is_secret"])
        self.assertFalse(sample["is_boolean"])

    # ---- auth -------------------------------------------------------------

    def test_get_rejects_unauthenticated(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )

    def test_get_rejects_non_staff_token(self):
        response = self._get(token=self.non_staff_token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    # ---- no-value-leakage (the core security contract) -------------------

    def test_get_does_not_echo_any_setting_value(self):
        # Plant a unique sentinel as a DB-stored value for a key that
        # would otherwise be unset. If the GET handler ever leaks
        # IntegrationSetting.value into the response, this assertion
        # catches it.
        sentinel = "SENTINEL-VALUE-do-NOT-leak-abc123XYZ"
        IntegrationSetting.objects.create(
            key="STRIPE_WEBHOOK_SECRET",
            value=sentinel,
            is_secret=True,
            group="stripe",
            description="",
        )

        response = self._get()
        self.assertEqual(response.status_code, 200)
        body_str = response.content.decode("utf-8")
        self.assertNotIn(sentinel, body_str)

        # And the entry still correctly reports `source == "db"` and
        # `configured == True` — the no-leak contract does NOT mean
        # "pretend it's not set".
        entry = self._entry_for(response.json(), "STRIPE_WEBHOOK_SECRET")
        self.assertTrue(entry["configured"])
        self.assertEqual(entry["source"], "db")

    def test_get_response_does_not_contain_value_substring(self):
        # The literal substring "value" must never appear in the
        # response body — neither as a JSON key nor inside any echoed
        # value. We plant a DB row first so the DB-source branch is
        # exercised by the same call.
        IntegrationSetting.objects.create(
            key="CONTENT_CDN_BASE",
            value="https://cdn.example.com",
            is_secret=False,
            group="s3_content",
            description="",
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("value", response.content.decode("utf-8"))

    # ---- source resolution per layer -------------------------------------

    def test_get_marks_db_override_with_source_db(self):
        # DB row with non-empty value beats every other layer.
        IntegrationSetting.objects.create(
            key="CONTENT_CDN_BASE",
            value="https://cdn.example.com",
            is_secret=False,
            group="s3_content",
            description="",
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        entry = self._entry_for(response.json(), "CONTENT_CDN_BASE")
        self.assertEqual(entry["source"], "db")
        self.assertTrue(entry["configured"])

    def test_get_marks_env_only_with_source_env(self):
        # Pick a key NOT defined as an attribute on django.conf.settings
        # so the env layer wins (settings layer is skipped).
        # SLACK_TEAM_ID is in the registry but absent from
        # website/settings.py and has no registry default — env is the
        # first hit.
        env_key = "SLACK_TEAM_ID"
        with patch.dict(os.environ, {env_key: "T01ABC123"}, clear=False):
            response = self._get()

        self.assertEqual(response.status_code, 200)
        entry = self._entry_for(response.json(), env_key)
        self.assertEqual(entry["source"], "env")
        self.assertTrue(entry["configured"])

    def test_get_marks_django_settings_with_source_django_settings(self):
        # Use a registry key NOT normally on django.conf.settings, so
        # override_settings injecting it cleanly demonstrates the
        # django_settings branch. SLACK_ANNOUNCEMENTS_CHANNEL_NAME has
        # no entry in website/settings.py and no registry default —
        # override_settings is the only source.
        settings_key = "SLACK_ANNOUNCEMENTS_CHANNEL_NAME"
        # Defensive: clear env for this key so it doesn't shadow the
        # settings layer in dev environments where it might be set.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(settings_key, None)
            with override_settings(
                **{settings_key: "#announcements-from-settings"}
            ):
                response = self._get()

        self.assertEqual(response.status_code, 200)
        entry = self._entry_for(response.json(), settings_key)
        self.assertEqual(entry["source"], "django_settings")
        self.assertTrue(entry["configured"])

    def test_get_marks_default_value_with_source_default(self):
        # GITHUB_APP_PRIVATE_KEY_SECRET_ID has a non-empty registry
        # default ('ai-shipping-labs/github-app-private-key') and is
        # NOT defined on django.conf.settings — so when nothing
        # overrides it, the source must resolve to "default".
        default_key = "GITHUB_APP_PRIVATE_KEY_SECRET_ID"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(default_key, None)
            response = self._get()

        self.assertEqual(response.status_code, 200)
        entry = self._entry_for(response.json(), default_key)
        self.assertEqual(entry["source"], "default")
        self.assertTrue(entry["configured"])

    def test_get_marks_unset_with_source_null_and_configured_false(self):
        # SLACK_TEAM_ID has no registry default, no
        # django.conf.settings entry, and we clear it from env — so
        # nothing is configured anywhere. source must be null and
        # configured must be false.
        unset_key = "SLACK_TEAM_ID"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(unset_key, None)
            response = self._get()

        self.assertEqual(response.status_code, 200)
        entry = self._entry_for(response.json(), unset_key)
        self.assertIsNone(entry["source"])
        self.assertFalse(entry["configured"])

    # ---- docs_url (issue #641) -------------------------------------------

    def test_get_includes_docs_url_for_stripe_keys(self):
        """Each Stripe key declares a per-anchor docs_url; the API surfaces it.

        The registry stores the path as
        ``_docs/integrations/stripe.md#<anchor>``. The GET endpoint
        passes the raw registry value through unchanged — Studio rewrites
        it to a /studio/docs/... URL at render time. External
        automation calling this API can use the path to deep-link into a
        rendered docs page or to locate the markdown source.
        """
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        for key in (
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "STRIPE_CUSTOMER_PORTAL_URL",
            "STRIPE_DASHBOARD_ACCOUNT_ID",
        ):
            with self.subTest(key=key):
                entry = self._entry_for(body, key)
                self.assertEqual(
                    entry["docs_url"],
                    f"_docs/integrations/stripe.md#{key.lower()}",
                )

    def test_get_returns_empty_docs_url_for_unauthored_keys(self):
        """Keys with no ``docs_url`` in the registry come back as ''.

        The field is always present so external clients can rely on the
        shape without per-entry conditionals; an empty string means
        "docs not yet authored", matching the Studio template's
        truthiness check that hides the (?) icon.
        """
        response = self._get()
        self.assertEqual(response.status_code, 200)
        # SLACK_ENABLED has not yet received a docs_url in this commit.
        entry = self._entry_for(response.json(), "SLACK_ENABLED")
        self.assertEqual(entry["docs_url"], "")

    # ---- POST behaviour MUST be unchanged after adding GET --------------

    def test_existing_post_behavior_unchanged_for_set_success(self):
        # Same payload and assertions as
        # test_post_writes_allowed_registry_key — duplicated here so
        # the GET-tests file fails loudly if the GET addition
        # accidentally broke the POST happy path.
        payload = {
            "updates": [
                {"key": "CONTENT_CDN_BASE", "value": "https://cdn.example.com"},
            ],
        }
        response = self.client.post(
            URL,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "updated": 1},
        )
        row = IntegrationSetting.objects.get(key="CONTENT_CDN_BASE")
        self.assertEqual(row.value, "https://cdn.example.com")

    def test_existing_post_behavior_unchanged_for_invalid_key(self):
        # Same contract as test_post_rejects_unknown_key_and_writes_nothing:
        # invalid_key error code, all-or-nothing, no DB rows written.
        starting_count = IntegrationSetting.objects.count()
        response = self.client.post(
            URL,
            data=json.dumps({
                "updates": [{"key": "DJANGO_SECRET_KEY", "value": "x"}],
            }),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "invalid_key")
        self.assertEqual(
            body["details"]["invalid_keys"],
            ["DJANGO_SECRET_KEY"],
        )
        self.assertEqual(IntegrationSetting.objects.count(), starting_count)
