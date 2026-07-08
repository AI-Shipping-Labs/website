"""Runtime services honor Studio-backed IntegrationSetting values."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.config import clear_config_cache
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
            "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
        }

        self.assertFalse(ses_service.validate_sns_notification(payload))
        mock_verify.assert_called_once_with(
            payload,
            "https://sns.us-east-1.amazonaws.com/cert.pem",
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

    @override_settings(
        SES_TRANSACTIONAL_FROM_EMAIL="settings@example.com",
        AWS_SES_REGION="us-east-1",
        # Issue #509: opt in so _send_raw_email exercises the boto3 path.
        SES_ENABLED=True,
    )
    @patch("events.services.registration_email.boto3.client")
    def test_registration_email_uses_db_sender_and_region(self, mock_boto_client):
        from events.services.registration_email import _send_raw_email

        mock_client = MagicMock()
        mock_client.send_email.return_value = {"MessageId": "ses-message-id"}
        mock_boto_client.return_value = mock_client
        set_integration("SES_TRANSACTIONAL_FROM_EMAIL", "events@example.com", "email")
        set_integration("AWS_SES_REGION", "eu-west-1", "email")

        message_id = _send_raw_email(
            to_email="member@example.com",
            subject="Event",
            html_body="<p>Event</p>",
            ics_content="BEGIN:VCALENDAR\nEND:VCALENDAR",
        )

        self.assertEqual(message_id, "ses-message-id")
        mock_boto_client.assert_called_once()
        self.assertEqual(mock_boto_client.call_args.kwargs["region_name"], "eu-west-1")
        self.assertEqual(
            mock_client.send_email.call_args.kwargs["FromEmailAddress"],
            "events@example.com",
        )

    @override_settings(SES_TRANSACTIONAL_FROM_EMAIL="settings@example.com")
    def test_calendar_invite_organizer_uses_db_sender(self):
        from events.services.calendar_invite import generate_ics

        set_integration("SES_TRANSACTIONAL_FROM_EMAIL", "calendar@example.com", "email")
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
