"""Tests for SES webhook endpoint - issue #85.

Covers:
- SES/SNS webhook endpoint: POST /api/webhooks/ses
- Hard bounce handling: sets user.unsubscribed = True
- Complaint handling: sets user.unsubscribed = True
- Soft bounce handling: does NOT unsubscribe
- SNS subscription confirmation
- Invalid payloads
- WebhookLog creation
- SNS signature validation service
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from integrations.models import WebhookLog

User = get_user_model()


def make_bounce_notification(email, bounce_type='Permanent'):
    """Create an SNS notification payload with a SES bounce event."""
    return {
        'Type': 'Notification',
        'MessageId': 'msg-123',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789:ses-bounces',
        'Timestamp': '2026-02-19T12:00:00.000Z',
        'Message': json.dumps({
            'notificationType': 'Bounce',
            'bounce': {
                'bounceType': bounce_type,
                'bouncedRecipients': [
                    {'emailAddress': email},
                ],
            },
        }),
    }


def make_complaint_notification(email):
    """Create an SNS notification payload with a SES complaint event."""
    return {
        'Type': 'Notification',
        'MessageId': 'msg-456',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789:ses-complaints',
        'Timestamp': '2026-02-19T12:00:00.000Z',
        'Message': json.dumps({
            'notificationType': 'Complaint',
            'complaint': {
                'complainedRecipients': [
                    {'emailAddress': email},
                ],
            },
        }),
    }


def make_subscription_confirmation():
    """Create an SNS SubscriptionConfirmation payload."""
    return {
        'Type': 'SubscriptionConfirmation',
        'MessageId': 'msg-sub-789',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789:ses-bounces',
        'Token': 'confirm-token-abc',
        'SubscribeURL': 'https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription&Token=abc',
        'Message': 'You have chosen to subscribe...',
        'Timestamp': '2026-02-19T12:00:00.000Z',
    }


@override_settings(SES_WEBHOOK_VALIDATION_ENABLED=False)
class SESWebhookBounceTest(TestCase):
    """Test hard bounce handling via SES webhook."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email='bounce@example.com')

    def test_hard_bounce_unsubscribes_user(self):
        payload = make_bounce_notification('bounce@example.com', 'Permanent')
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)

    def test_soft_bounce_does_not_unsubscribe(self):
        payload = make_bounce_notification('bounce@example.com', 'Transient')
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertFalse(self.user.unsubscribed)

    def test_hard_bounce_creates_webhook_log(self):
        payload = make_bounce_notification('bounce@example.com')
        self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )

        log = WebhookLog.objects.filter(service='ses').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, 'Bounce')
        self.assertTrue(log.processed)

    def test_bounce_for_unknown_email(self):
        payload = make_bounce_notification('unknown@example.com')
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        # Should still create webhook log
        self.assertEqual(WebhookLog.objects.filter(service='ses').count(), 1)

    def test_already_unsubscribed_user_stays_unsubscribed(self):
        self.user.unsubscribed = True
        self.user.save()

        payload = make_bounce_notification('bounce@example.com')
        self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)


@override_settings(SES_WEBHOOK_VALIDATION_ENABLED=False)
class SESWebhookComplaintTest(TestCase):
    """Test complaint handling via SES webhook."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email='complaint@example.com')

    def test_complaint_unsubscribes_user(self):
        payload = make_complaint_notification('complaint@example.com')
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)

    def test_complaint_creates_webhook_log(self):
        payload = make_complaint_notification('complaint@example.com')
        self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )

        log = WebhookLog.objects.filter(
            service='ses', event_type='Complaint',
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.processed)

    def test_complaint_multiple_recipients(self):
        user2 = User.objects.create_user(email='complaint2@example.com')
        payload = {
            'Type': 'Notification',
            'MessageId': 'msg-multi',
            'TopicArn': 'arn:aws:sns:us-east-1:123:topic',
            'Timestamp': '2026-02-19T12:00:00.000Z',
            'Message': json.dumps({
                'notificationType': 'Complaint',
                'complaint': {
                    'complainedRecipients': [
                        {'emailAddress': 'complaint@example.com'},
                        {'emailAddress': 'complaint2@example.com'},
                    ],
                },
            }),
        }
        self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.user.refresh_from_db()
        user2.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertTrue(user2.unsubscribed)


@override_settings(SES_WEBHOOK_VALIDATION_ENABLED=False)
class SESWebhookEndpointTest(TestCase):
    """Test general SES webhook endpoint behavior."""

    def setUp(self):
        self.client = Client()

    def test_get_not_allowed(self):
        response = self.client.get('/api/webhooks/ses')
        self.assertEqual(response.status_code, 405)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            '/api/webhooks/ses',
            data='not-json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_csrf_exempt(self):
        """Webhook endpoint must work without CSRF token."""
        payload = make_bounce_notification('nobody@example.com')
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_unknown_notification_type_ignored(self):
        payload = {
            'Type': 'Notification',
            'MessageId': 'msg-unknown',
            'TopicArn': 'arn:aws:sns:us-east-1:123:topic',
            'Timestamp': '2026-02-19T12:00:00.000Z',
            'Message': json.dumps({
                'notificationType': 'Delivery',
                'delivery': {},
            }),
        }
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_subscription_confirmation(self):
        payload = make_subscription_confirmation()
        # Patch the requests.get call for subscription confirmation
        from unittest.mock import patch, MagicMock
        with patch('integrations.views.ses_webhook.requests') as mock_requests:
            mock_requests.get.return_value = MagicMock(status_code=200)
            response = self.client.post(
                '/api/webhooks/ses',
                data=json.dumps(payload),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'subscription_confirmed')

    def test_unknown_message_type_ignored(self):
        payload = {
            'Type': 'UnknownType',
            'MessageId': 'msg-unk',
        }
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ignored')


@override_settings(SES_WEBHOOK_VALIDATION_ENABLED=True)
class SESWebhookValidationTest(TestCase):
    """Test that validation is enforced when enabled."""

    def setUp(self):
        self.client = Client()

    def test_invalid_signature_rejected(self):
        """When validation is enabled, a payload without proper
        signing data should be rejected."""
        payload = make_bounce_notification('test@example.com')
        # No SigningCertURL or Signature in payload
        response = self.client.post(
            '/api/webhooks/ses',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)


class SNSValidationServiceTest(TestCase):
    """Test the SNS notification validation service."""

    def test_valid_cert_url(self):
        from integrations.services.ses import _is_valid_cert_url
        self.assertTrue(_is_valid_cert_url(
            'https://sns.us-east-1.amazonaws.com/cert.pem',
        ))

    def test_invalid_cert_url_wrong_host(self):
        from integrations.services.ses import _is_valid_cert_url
        self.assertFalse(_is_valid_cert_url(
            'https://evil.example.com/cert.pem',
        ))

    def test_invalid_cert_url_http(self):
        from integrations.services.ses import _is_valid_cert_url
        self.assertFalse(_is_valid_cert_url(
            'http://sns.us-east-1.amazonaws.com/cert.pem',
        ))

    def test_invalid_cert_url_empty(self):
        from integrations.services.ses import _is_valid_cert_url
        self.assertFalse(_is_valid_cert_url(''))

    @override_settings(DEBUG=True, SES_WEBHOOK_VALIDATION_ENABLED=False)
    def test_validation_skipped_in_debug(self):
        from integrations.services.ses import validate_sns_notification
        # Should return True without any signing data
        self.assertTrue(validate_sns_notification({}))

    def test_build_signing_string_notification(self):
        from integrations.services.ses import _build_signing_string
        payload = {
            'Type': 'Notification',
            'MessageId': 'msg-1',
            'Message': 'test message',
            'Timestamp': '2026-01-01T00:00:00.000Z',
            'TopicArn': 'arn:aws:sns:us-east-1:123:topic',
        }
        result = _build_signing_string(payload)
        self.assertIn('Message\ntest message\n', result)
        self.assertIn('Type\nNotification\n', result)

    def test_build_signing_string_notification_with_subject(self):
        from integrations.services.ses import _build_signing_string
        payload = {
            'Type': 'Notification',
            'MessageId': 'msg-1',
            'Message': 'test',
            'Subject': 'Test Subject',
            'Timestamp': '2026-01-01T00:00:00.000Z',
            'TopicArn': 'arn:aws:sns:us-east-1:123:topic',
        }
        result = _build_signing_string(payload)
        self.assertIn('Subject\nTest Subject\n', result)

    def test_build_signing_string_subscription_confirmation(self):
        from integrations.services.ses import _build_signing_string
        payload = {
            'Type': 'SubscriptionConfirmation',
            'MessageId': 'msg-1',
            'Message': 'confirm',
            'SubscribeURL': 'https://example.com/confirm',
            'Timestamp': '2026-01-01T00:00:00.000Z',
            'Token': 'token-abc',
            'TopicArn': 'arn:aws:sns:us-east-1:123:topic',
        }
        result = _build_signing_string(payload)
        self.assertIn('Token\ntoken-abc\n', result)
        self.assertIn('SubscribeURL\nhttps://example.com/confirm\n', result)

    def test_build_signing_string_unknown_type(self):
        from integrations.services.ses import _build_signing_string
        payload = {'Type': 'Unknown'}
        result = _build_signing_string(payload)
        self.assertIsNone(result)
