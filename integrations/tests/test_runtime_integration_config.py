"""Runtime services honor Studio-backed IntegrationSetting values."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.config import (
    clear_config_cache,
    get_bool_config,
    get_config,
    get_int_config,
    is_enabled,
    reset_local_config_cache,
)
from integrations.models import IntegrationSetting
from integrations.services import ses as ses_service
from payments.services import _get_stripe_client, verify_webhook_signature
from tests.fixtures import TierSetupMixin

User = get_user_model()


def set_integration(key, value, group):
    """Create or update a Studio integration setting."""
    setting, _ = IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={"value": value, "group": group},
    )
    return setting


class RuntimeConfigTestCase(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()


class StripeRuntimeConfigTest(TierSetupMixin, RuntimeConfigTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="stripe-runtime@test.com",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.login(email="stripe-runtime@test.com", password="testpass123")

    @override_settings(STRIPE_SECRET_KEY="sk_settings")
    @patch("payments.services.stripe.StripeClient")
    def test_stripe_secret_key_uses_db_value_and_cache_refresh(self, mock_client):
        set_integration("STRIPE_SECRET_KEY", "sk_db_old", "stripe")
        _get_stripe_client()
        mock_client.assert_called_with("sk_db_old")

        IntegrationSetting.objects.filter(key="STRIPE_SECRET_KEY").update(
            value="sk_db_new",
        )
        clear_config_cache()
        _get_stripe_client()
        self.assertEqual(mock_client.call_args_list[-1].args, ("sk_db_new",))

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_settings")
    @patch("payments.services.stripe.Webhook.construct_event")
    def test_webhook_signature_uses_db_secret(self, mock_construct_event):
        mock_construct_event.return_value = {"id": "evt_test"}
        set_integration("STRIPE_WEBHOOK_SECRET", "whsec_db", "stripe")

        event = verify_webhook_signature(b"{}", "t=1,v1=test")

        self.assertEqual(event["id"], "evt_test")
        mock_construct_event.assert_called_once_with(
            b"{}",
            "t=1,v1=test",
            "whsec_db",
        )


class SESRuntimeConfigTest(RuntimeConfigTestCase):
    @override_settings(DEBUG=False)
    @patch("integrations.services.ses._verify_signature")
    def test_ses_validation_defaults_to_enabled_outside_debug(self, mock_verify):
        mock_verify.return_value = False
        payload = {
            "SigningCertURL": (
                "https://sns.us-east-1.amazonaws.com/"
                "SimpleNotificationService-0123456789abcdef.pem"
            ),
        }

        self.assertFalse(ses_service.validate_sns_notification(payload))
        mock_verify.assert_called_once_with(
            payload,
            (
                "https://sns.us-east-1.amazonaws.com/"
                "SimpleNotificationService-0123456789abcdef.pem"
            ),
        )

    @override_settings(DEBUG=True)
    @patch("integrations.services.ses._verify_signature")
    def test_ses_validation_defaults_to_disabled_in_debug(self, mock_verify):
        self.assertTrue(ses_service.validate_sns_notification({}))
        mock_verify.assert_not_called()

    @override_settings(DEBUG=False, SES_WEBHOOK_VALIDATION_ENABLED=True)
    @patch("integrations.services.ses._verify_signature")
    def test_ses_validation_can_be_disabled_from_db(self, mock_verify):
        set_integration("SES_WEBHOOK_VALIDATION_ENABLED", "false", "email")

        self.assertTrue(ses_service.validate_sns_notification({}))
        mock_verify.assert_not_called()

    @override_settings(DEBUG=False, SES_WEBHOOK_VALIDATION_ENABLED=False)
    @patch("integrations.services.ses._verify_signature")
    def test_ses_validation_override_settings_false_still_disables(self, mock_verify):
        self.assertTrue(ses_service.validate_sns_notification({}))
        mock_verify.assert_not_called()

    @override_settings(DEBUG=False, SES_WEBHOOK_VALIDATION_ENABLED=False)
    def test_ses_validation_db_true_overrides_settings_fallback(self):
        set_integration("SES_WEBHOOK_VALIDATION_ENABLED", "1", "email")

        self.assertFalse(ses_service.validate_sns_notification({}))

    def test_registration_email_uses_db_relay_url_and_key(self):
        from events.services.registration_email import _send_calendar_email

        set_integration("RELAY_BASE_URL", "https://db-relay.example", "email")
        set_integration("RELAY_API_KEY", "db-relay-key", "email")
        response = MagicMock(status_code=202)
        response.json.return_value = {
            "message": {
                "id": "relay-message-id",
                "status": "queued",
                "template_key": "event_registration",
                "idempotency_key": "runtime-registration:1",
            },
            "idempotent_replay": False,
        }

        with patch(
            "email_app.relay_transactional.requests.post",
            return_value=response,
        ) as post:
            message_id = _send_calendar_email(
                to_email="member@example.com",
                template_key="event_registration",
                context={"event_title": "Event"},
                ics_content="BEGIN:VCALENDAR\nEND:VCALENDAR",
                idempotency_key="runtime-registration:1",
                safe_correlation="event=1 user=1",
            )

        self.assertEqual(message_id, "relay-message-id")
        self.assertEqual(post.call_args.args[0], "https://db-relay.example/api/transactional/send")
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer db-relay-key",
        )

    @override_settings(RELAY_TRANSACTIONAL_FROM_EMAIL="settings@example.com")
    def test_calendar_invite_organizer_uses_db_sender(self):
        from events.services.calendar_invite import generate_ics

        set_integration("RELAY_TRANSACTIONAL_FROM_EMAIL", "calendar@example.com", "email")
        event = SimpleNamespace(
            title="Runtime Config Event",
            slug="runtime-config-event",
            start_datetime=timezone.now(),
            end_datetime=None,
            ics_sequence=0,
            description="",
        )

        ics = generate_ics(event).decode("utf-8")

        self.assertIn("mailto:calendar@example.com", ics)


class ResetLocalConfigCacheTest(RuntimeConfigTestCase):
    """``reset_local_config_cache`` is the DB-free half of the cache clear (#1470)."""

    def test_reset_drops_a_stale_db_override_from_the_in_process_cache(self):
        """A deleted IntegrationSetting must stop shadowing settings/env.

        This is the leak that made Playwright modules order-dependent: DB
        overrides win over ``settings`` in ``get_config()``, and nothing
        invalidated the module-level cache when a test flushed the table.
        """
        set_integration("SLACK_INVITE_URL", "https://join.slack.com/db-value", "slack")
        clear_config_cache()
        self.assertEqual(
            get_config("SLACK_INVITE_URL"), "https://join.slack.com/db-value"
        )

        IntegrationSetting.objects.filter(key="SLACK_INVITE_URL").delete()
        # Without the reset the deleted row is still served from _cache.
        self.assertEqual(
            get_config("SLACK_INVITE_URL"), "https://join.slack.com/db-value"
        )

        reset_local_config_cache()
        with override_settings(SLACK_INVITE_URL="https://join.slack.com/settings"):
            self.assertEqual(
                get_config("SLACK_INVITE_URL"), "https://join.slack.com/settings"
            )

    def test_reset_publishes_no_cross_process_stamp(self):
        """It must be usable from tests that have no database access."""
        with patch("django.core.cache.caches") as caches:
            reset_local_config_cache()
        caches.__getitem__.assert_not_called()

    def test_clear_config_cache_still_publishes_a_stamp(self):
        """The Studio-save path keeps its cross-process invalidation."""
        from django.core.cache import caches

        caches["django_q"].delete("integration_settings_stamp")
        clear_config_cache()
        self.assertIsNotNone(caches["django_q"].get("integration_settings_stamp"))


class GetBoolConfigConsolidationTest(RuntimeConfigTestCase):
    """D15: ``get_bool_config`` is the single boolean config parser."""

    def test_get_bool_config_pins_truthy_and_falsy_tokens_and_defaults(self):
        self.assertFalse(get_bool_config("D15_UNSET_KEY"))
        self.assertTrue(get_bool_config("D15_UNSET_KEY", default=True))
        self.assertFalse(is_enabled("D15_UNSET_KEY"))

        for raw in ("true", "TRUE", " 1 ", "Yes", True):
            with override_settings(D15_BOOL_TEST=raw):
                self.assertTrue(get_bool_config("D15_BOOL_TEST", default=False))
        for raw in ("false", "0", "no", "", "random", False):
            with override_settings(D15_BOOL_TEST=raw):
                self.assertFalse(get_bool_config("D15_BOOL_TEST", default=True))

    def test_ses_validation_override_settings_beats_env_var(self):
        """Edge case: ``_ses_validation_enabled`` now follows DB -> settings -> env."""
        with patch.dict(os.environ, {"SES_WEBHOOK_VALIDATION_ENABLED": "true"}):
            with override_settings(DEBUG=False, SES_WEBHOOK_VALIDATION_ENABLED=False):
                self.assertFalse(ses_service._ses_validation_enabled())

        with patch.dict(os.environ, {"SES_WEBHOOK_VALIDATION_ENABLED": "false"}):
            with override_settings(DEBUG=False, SES_WEBHOOK_VALIDATION_ENABLED=True):
                self.assertTrue(ses_service._ses_validation_enabled())


class GetIntConfigConsolidationTest(RuntimeConfigTestCase):
    """D16: ``get_int_config`` is the single integer config parser."""

    def test_get_int_config_pins_bounds_and_decimal_parsing(self):
        self.assertEqual(get_int_config("D16_UNSET_KEY", 10), 10)
        with override_settings(D16_INT_TEST=" 25 "):
            self.assertEqual(get_int_config("D16_INT_TEST", 10, minimum=1, maximum=30), 25)
            self.assertEqual(get_int_config("D16_INT_TEST", 10, minimum=1, maximum=20), 10)
        with override_settings(D16_INT_TEST="0"):
            self.assertEqual(get_int_config("D16_INT_TEST", 10, minimum=1), 10)
            self.assertEqual(get_int_config("D16_INT_TEST", 10, minimum=0), 0)

    def test_divergent_callers_reject_floats_and_booleans(self):
        """Edge case: ``ingest_plan_sprints`` and ``sync_config`` no longer coerce floats/bools."""
        from crm.tasks.ingest_plan_sprints import (
            FIRST_RUN_LOOKBACK_DAYS,
            _first_run_lookback_days,
        )
        from crm.tasks.ingest_plan_sprints import (
            _positive_int_config as crm_positive_int,
        )
        from integrations.sync_config import (
            DEFAULT_SYNC_QUEUED_THRESHOLD_MINUTES,
            sync_queued_threshold_minutes,
        )

        for invalid in (3.9, "3.9", True, False, "0", "-5", "abc", ""):
            with self.subTest(value=invalid):
                with override_settings(
                    PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS=invalid,
                    SYNC_QUEUED_THRESHOLD_MINUTES=invalid,
                ):
                    self.assertEqual(_first_run_lookback_days(), FIRST_RUN_LOOKBACK_DAYS)
                    self.assertEqual(
                        crm_positive_int("PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS", 7),
                        7,
                    )
                    self.assertEqual(
                        sync_queued_threshold_minutes(),
                        DEFAULT_SYNC_QUEUED_THRESHOLD_MINUTES,
                    )


