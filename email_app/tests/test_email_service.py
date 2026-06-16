"""Tests for the EmailService.

Covers:
- Template loading and rendering with context variables
- Subject line rendering from frontmatter
- Markdown-to-HTML conversion
- HTML email wrapping with header, footer, unsubscribe link
- EmailLog creation on send
- Skipping unsubscribed users
- SES API call (mocked with boto3)
- Missing template error handling
- All 6 transactional template types
"""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
    get_sender_for_email_type,
)
from email_app.services.email_service import EmailService, EmailServiceError
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

INTERNAL_FOOTER_LEAKS = (
    'Issue #450',
    'the verify CTA must render',
    '{#',
    '#}',
)


def assert_no_internal_footer_text(test_case, html):
    for marker in INTERNAL_FOOTER_LEAKS:
        test_case.assertNotIn(marker, html)


@tag('core')
class EmailServiceSendTest(TestCase):
    """Test EmailService.send() method."""

    def setUp(self):
        clear_config_cache()
        self.user = User.objects.create_user(
            email='alice@example.com',
            first_name='Alice',
        )
        self.service = EmailService()

    def tearDown(self):
        clear_config_cache()

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-001')
    def test_send_creates_email_log(self, mock_ses):
        log = self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.email_type, 'welcome')
        self.assertEqual(log.ses_message_id, 'ses-msg-001')
        self.assertEqual(EmailLog.objects.count(), 1)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-002')
    def test_send_calls_ses_with_correct_email(self, mock_ses):
        self.service.send(self.user, 'welcome', {'tier_name': 'Basic'})

        mock_ses.assert_called_once()
        to_email, subject, html = mock_ses.call_args[0]
        self.assertEqual(to_email, 'alice@example.com')
        self.assertIn('Welcome to Basic', subject)
        self.assertIn('Basic', html)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-003')
    def test_transactional_send_delivers_to_unsubscribed_user(self, mock_ses):
        self.user.unsubscribed = True
        self.user.save()

        result = self.service.send(
            self.user,
            'password_reset',
            {'reset_url': 'https://example.test/reset'},
        )

        self.assertIsNotNone(result)
        mock_ses.assert_called_once()
        self.assertEqual(EmailLog.objects.count(), 1)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-004')
    def test_send_renders_user_name_in_body(self, mock_ses):
        self.service.send(self.user, 'welcome', {'tier_name': 'Premium'})

        call_args = mock_ses.call_args
        html_body = call_args[0][2]  # full HTML
        self.assertIn('Alice', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-004b')
    def test_send_renders_full_display_name_in_body(self, mock_ses):
        user = User.objects.create_user(
            email='ada@example.com',
            first_name='Ada',
            last_name='Lovelace',
        )

        self.service.send(user, 'welcome', {'tier_name': 'Premium'})

        html_body = mock_ses.call_args[0][2]
        self.assertIn('Ada Lovelace', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-005')
    def test_send_renders_user_email_fallback_name(self, mock_ses):
        user = User.objects.create_user(email='bob@example.com')
        self.service.send(user, 'welcome', {'tier_name': 'Free'})

        call_args = mock_ses.call_args
        html_body = call_args[0][2]
        self.assertIn('bob', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-005b')
    def test_send_renders_user_name_fallback_for_whitespace_name(self, mock_ses):
        user = User.objects.create_user(
            email='builder@example.com',
            first_name='  ',
            last_name='  ',
        )

        self.service.send(user, 'welcome', {'tier_name': 'Free'})

        html_body = mock_ses.call_args[0][2]
        self.assertIn('builder', html_body)

    def test_send_unknown_template_kind_raises_error(self):
        with self.assertRaises(EmailServiceError) as ctx:
            self.service.send(self.user, 'nonexistent_template', {})
        self.assertIn('not classified', str(ctx.exception))

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-006')
    def test_transactional_send_excludes_unsubscribe_link(self, mock_ses):
        self.service.send(
            self.user,
            'password_reset',
            {'reset_url': 'https://example.test/reset'},
        )

        call_args = mock_ses.call_args
        html_body = call_args[0][2]
        self.assertNotIn('Unsubscribe', html_body)
        self.assertNotIn('/api/unsubscribe?token=', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-006b')
    def test_transactional_send_passes_email_type_and_no_unsubscribe(self, mock_ses):
        # Issue #937: send() threads the template name through as email_type
        # so _send_ses can resolve the From address per type. The From
        # address itself (welcome@ vs noreply@) is asserted in the
        # SES-integration tests where the real boto3 payload is captured.
        self.service.send(
            self.user,
            'password_reset',
            {'reset_url': 'https://example.test/reset'},
        )

        self.assertEqual(mock_ses.call_args.kwargs['email_type'], 'password_reset')
        self.assertIsNone(mock_ses.call_args.kwargs['unsubscribe_url'])

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-007')
    def test_send_includes_header_and_footer(self, mock_ses):
        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        call_args = mock_ses.call_args
        html_body = call_args[0][2]
        # Header
        self.assertIn('AI Shipping Labs', html_body)
        self.assertIn('email-header', html_body)
        # Footer
        self.assertIn('email-footer', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-008')
    def test_send_default_context(self, mock_ses):
        """send() with no context dict should still work."""
        log = self.service.send(self.user, 'welcome')

        self.assertIsNotNone(log)
        mock_ses.assert_called_once()
        self.assertEqual(mock_ses.call_args[0][0], 'alice@example.com')
        self.assertIn('Welcome', mock_ses.call_args[0][1])

    def test_ambiguous_templates_are_explicitly_transactional(self):
        self.assertEqual(
            classify_email_type('community_invite'),
            EMAIL_KIND_TRANSACTIONAL,
        )
        self.assertEqual(
            classify_email_type('lead_magnet_delivery'),
            EMAIL_KIND_TRANSACTIONAL,
        )


class EmailServiceTemplateRenderingTest(TestCase):
    """Test template rendering for all transactional email types."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='render@example.com',
            first_name='Tester',
        )
        self.service = EmailService()

    @patch.object(EmailService, '_send_ses', return_value='test-id')
    def test_welcome_template(self, mock_ses):
        self.service.send(self.user, 'welcome', {
            'tier_name': 'Main',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('Welcome to Main', subject)
        self.assertIn('Tester', html)
        # #954: the gated /community/slack redirect, not a raw invite URL.
        self.assertIn('/community/slack', html)

    @patch.object(EmailService, '_send_ses', return_value='test-id')
    def test_payment_failed_template(self, mock_ses):
        self.service.send(self.user, 'payment_failed', {
            'tier_name': 'Premium',
            'update_payment_url': 'https://billing.stripe.com/update',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('Payment issue', subject)
        self.assertIn('Premium', html)
        self.assertIn('billing.stripe.com/update', html)

    @patch.object(EmailService, '_send_ses', return_value='test-id')
    def test_cancellation_template(self, mock_ses):
        self.service.send(self.user, 'cancellation', {
            'tier_name': 'Main',
            'access_until': 'March 15, 2026',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('cancelled', subject)
        self.assertIn('March 15, 2026', html)

    @patch.object(EmailService, '_send_ses', return_value='test-id')
    def test_community_invite_template(self, mock_ses):
        # Issue #953: the invite links to the gated /community/slack
        # redirect (built from the injected site_url), never the raw
        # SLACK_INVITE_URL — even if a caller still passes one.
        self.service.send(self.user, 'community_invite', {
            'slack_invite_url': 'https://slack.com/join/abc',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('community', subject)
        self.assertIn('/community/slack', html)
        self.assertNotIn('slack.com/join/abc', html)

    @patch.object(EmailService, '_send_ses', return_value='test-id')
    def test_lead_magnet_delivery_template(self, mock_ses):
        self.service.send(self.user, 'lead_magnet_delivery', {
            'resource_title': 'AI Cheat Sheet',
            'download_url': 'https://aishippinglabs.com/download/ai-cheat-sheet',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('download is ready', subject)
        self.assertIn('AI Cheat Sheet', html)
        self.assertIn('download/ai-cheat-sheet', html)

    @patch.object(EmailService, '_send_ses', return_value='test-id')
    def test_event_reminder_template(self, mock_ses):
        self.service.send(self.user, 'event_reminder', {
            'event_title': 'AI Workshop',
            'event_datetime': 'March 20, 2026 at 6:00 PM',
            'event_url': 'https://zoom.us/j/123',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('AI Workshop', subject)
        self.assertIn('March 20, 2026', html)
        self.assertIn('zoom.us/j/123', html)


@override_settings(SES_ENABLED=True)
class EmailServiceSESIntegrationTest(TestCase):
    """Test SES API integration (mocked).

    The whole class opts in to SES_ENABLED=True (issue #509). The kill-switch
    defaults False under TESTING; without the override every test in this
    suite would short-circuit before reaching the boto3 mock and fail.
    """

    def setUp(self):
        clear_config_cache()
        self.user = User.objects.create_user(email='ses@example.com')
        self.service = EmailService()

    def tearDown(self):
        clear_config_cache()

    @override_settings(AWS_ACCESS_KEY_ID='', AWS_SECRET_ACCESS_KEY='', AWS_SES_REGION='us-east-1')
    @patch('email_app.services.email_service.boto3')
    def test_ses_client_lazy_init(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Access ses_client property twice
        client1 = self.service.ses_client
        client2 = self.service.ses_client

        # boto3.client should only be called once
        mock_boto3.client.assert_called_once_with(
            'sesv2',
            region_name='us-east-1',
            aws_access_key_id='',
            aws_secret_access_key='',
        )
        self.assertIs(client1, client2)

    @patch('email_app.services.email_service.boto3')
    def test_ses_client_uses_integration_settings(self, mock_boto3):
        IntegrationSetting.objects.create(
            key='AWS_SES_REGION',
            value='eu-west-1',
            group='ses',
        )
        IntegrationSetting.objects.create(
            key='AWS_ACCESS_KEY_ID',
            value='db-key',
            group='ses',
            is_secret=True,
        )
        IntegrationSetting.objects.create(
            key='AWS_SECRET_ACCESS_KEY',
            value='db-secret',
            group='ses',
            is_secret=True,
        )
        clear_config_cache()

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        _ = self.service.ses_client

        mock_boto3.client.assert_called_once_with(
            'sesv2',
            region_name='eu-west-1',
            aws_access_key_id='db-key',
            aws_secret_access_key='db-secret',
        )

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_calls_api(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'ses-real-id'}
        mock_boto3.client.return_value = mock_client

        result = self.service._send_ses(
            'recipient@example.com',
            'Test Subject',
            '<html><body>Hello</body></html>',
        )

        self.assertEqual(result, 'ses-real-id')
        mock_client.send_email.assert_called_once()
        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['Destination']['ToAddresses'],
            ['recipient@example.com'],
        )
        self.assertEqual(
            call_kwargs['Content']['Simple']['Subject']['Data'],
            'Test Subject',
        )
        self.assertNotIn('Headers', call_kwargs['Content']['Simple'])

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_adds_unsubscribe_headers_when_url_provided(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'ses-real-id'}
        mock_boto3.client.return_value = mock_client

        unsubscribe_url = 'https://example.test/api/unsubscribe?token=abc'
        self.service._send_ses(
            'recipient@example.com',
            'Test Subject',
            '<html><body>Hello</body></html>',
            unsubscribe_url=unsubscribe_url,
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['Content']['Simple']['Headers'],
            [
                {
                    'Name': 'List-Unsubscribe',
                    'Value': f'<{unsubscribe_url}>',
                },
                {
                    'Name': 'List-Unsubscribe-Post',
                    'Value': 'List-Unsubscribe=One-Click',
                },
            ],
        )

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_adds_optional_mailto_unsubscribe_header(self, mock_boto3):
        IntegrationSetting.objects.create(
            key='SES_UNSUBSCRIBE_EMAIL',
            value='unsubscribe@aishippinglabs.com',
            group='ses',
        )
        clear_config_cache()

        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'ses-real-id'}
        mock_boto3.client.return_value = mock_client

        unsubscribe_url = 'https://example.test/api/unsubscribe?token=abc'
        self.service._send_ses(
            'recipient@example.com',
            'Test Subject',
            '<html><body>Hello</body></html>',
            unsubscribe_url=unsubscribe_url,
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['Content']['Simple']['Headers'][0]['Value'],
            f'<{unsubscribe_url}>, '
            '<mailto:unsubscribe@aishippinglabs.com>',
        )

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_error_raises_exception(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.side_effect = ClientError(
            {
                'Error': {
                    'Code': 'ThrottlingException',
                    'Message': 'SES down',
                },
            },
            'SendEmail',
        )
        mock_boto3.client.return_value = mock_client

        with (
            self.assertLogs('email_app.services.email_service', level='ERROR') as logs,
            self.assertRaises(EmailServiceError) as ctx,
        ):
            self.service._send_ses('to@example.com', 'Sub', '<html/>')
        self.assertIn('Failed to send email via SES to to@example.com', logs.output[0])
        self.assertIn('SES send failed', str(ctx.exception))

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_unexpected_error_propagates(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.side_effect = RuntimeError('bad send kwargs')
        mock_boto3.client.return_value = mock_client

        with self.assertRaisesRegex(RuntimeError, 'bad send kwargs'):
            self.service._send_ses('to@example.com', 'Sub', '<html/>')

    @override_settings(SES_TRANSACTIONAL_FROM_EMAIL='custom@example.com')
    @patch('email_app.services.email_service.boto3')
    def test_send_ses_uses_configured_transactional_from_email(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses(
            'to@example.com',
            'Sub',
            '<html/>',
            email_type='password_reset',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['FromEmailAddress'], 'custom@example.com')

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_uses_integration_setting_transactional_from_email(self, mock_boto3):
        IntegrationSetting.objects.create(
            key='SES_TRANSACTIONAL_FROM_EMAIL',
            value='sender@example.com',
            group='ses',
        )
        clear_config_cache()

        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses(
            'to@example.com',
            'Sub',
            '<html/>',
            email_type='password_reset',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['FromEmailAddress'], 'sender@example.com')

    @override_settings(SES_PROMOTIONAL_FROM_EMAIL='promo@example.com')
    @patch('email_app.services.email_service.boto3')
    def test_send_ses_uses_configured_promotional_from_email(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses(
            'to@example.com',
            'Sub',
            '<html/>',
            email_type='campaign',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['FromEmailAddress'], 'promo@example.com')

    @override_settings(
        SES_FROM_EMAIL='legacy@example.com',
        SES_TRANSACTIONAL_FROM_EMAIL='',
        SES_PROMOTIONAL_FROM_EMAIL='',
    )
    @patch('email_app.services.email_service.boto3')
    def test_send_ses_uses_legacy_from_email_fallback(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses(
            'to@example.com',
            'Sub',
            '<html/>',
            email_type='password_reset',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['FromEmailAddress'], 'legacy@example.com')

    @patch('email_app.services.email_service.boto3')
    def test_send_email_passes_configuration_set_when_set(self, mock_boto3):
        IntegrationSetting.objects.create(
            key='SES_CONFIGURATION_SET_NAME',
            value='my-set',
            group='ses',
        )
        clear_config_cache()

        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses('to@example.com', 'Sub', '<html/>')

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['ConfigurationSetName'], 'my-set')

    @patch('email_app.services.email_service.boto3')
    def test_send_email_omits_configuration_set_when_empty(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses('to@example.com', 'Sub', '<html/>')

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertNotIn('ConfigurationSetName', call_kwargs)

    # -- Issue #937: per-type welcome sender on the send chokepoint ---------

    @patch('email_app.services.email_service.boto3')
    def test_welcome_send_uses_welcome_from_address(self, mock_boto3):
        """A welcome-type send through the full send() path must hand SES
        the dedicated welcome@ From address."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-w'}
        mock_boto3.client.return_value = mock_client

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'welcome@aishippinglabs.com',
        )

    @patch('email_app.services.email_service.boto3')
    def test_password_reset_send_uses_noreply_from_address(self, mock_boto3):
        """A non-welcome transactional send is unchanged: noreply@."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-pr'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'password_reset',
            {'reset_url': 'https://example.test/reset'},
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'noreply@aishippinglabs.com',
        )

    @patch('email_app.services.email_service.boto3')
    def test_welcome_send_to_unsubscribed_user_still_sends(self, mock_boto3):
        """Delivery semantics preserved: an unsubscribed user is NOT skipped
        for a welcome send — SES is invoked and an EmailLog is returned."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-wu'}
        mock_boto3.client.return_value = mock_client

        self.user.unsubscribed = True
        self.user.save(update_fields=['unsubscribed'])

        log = self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        self.assertIsNotNone(log)
        mock_client.send_email.assert_called_once()
        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'welcome@aishippinglabs.com',
        )

    @patch('email_app.services.email_service.boto3')
    def test_promotional_send_to_unsubscribed_user_skipped(self, mock_boto3):
        """Regression control: an unsubscribed user IS skipped for a
        promotional send (SES not invoked, no EmailLog)."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-cu'}
        mock_boto3.client.return_value = mock_client

        self.user.unsubscribed = True
        self.user.save(update_fields=['unsubscribed'])

        log = self.service.send(self.user, 'campaign', {'subject': 'Hi'})

        self.assertIsNone(log)
        mock_client.send_email.assert_not_called()

    @patch('email_app.services.email_service.boto3')
    def test_welcome_send_has_no_unsubscribe_header(self, mock_boto3):
        """A welcome send is still transactional: no List-Unsubscribe header
        and no unsubscribe footer in the body."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-wh'}
        mock_boto3.client.return_value = mock_client

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        call_kwargs = mock_client.send_email.call_args[1]
        headers = call_kwargs['Content']['Simple'].get('Headers', [])
        header_names = {h['Name'] for h in headers}
        self.assertNotIn('List-Unsubscribe', header_names)
        self.assertNotIn('List-Unsubscribe-Post', header_names)

        html_body = call_kwargs['Content']['Simple']['Body']['Html']['Data']
        self.assertNotIn('/api/unsubscribe?token=', html_body)

    @override_settings(SES_WELCOME_FROM_EMAIL='hello@aishippinglabs.com')
    @patch('email_app.services.email_service.boto3')
    def test_welcome_send_honours_db_or_env_welcome_override(self, mock_boto3):
        """The welcome From address is resolved through get_config, so an
        override (env here) flows to the SES payload with no code change."""
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-ov'}
        mock_boto3.client.return_value = mock_client

        # Sanity: the resolver agrees with what _send_ses will use.
        self.assertEqual(
            get_sender_for_email_type('welcome'),
            'hello@aishippinglabs.com',
        )

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'hello@aishippinglabs.com',
        )

    # -- Issue #950: cofounder_welcome From regression --------------------

    @patch('email_app.services.email_service.boto3')
    def test_cofounder_welcome_sends_from_welcome_address(self, mock_boto3):
        """Regression (issue #950 part 1): a cofounder_welcome send resolves
        the SES From to welcome@ end-to-end, i.e. the email_type is actually
        threaded through send() -> _send_ses() -> get_sender_for_email_type.
        """
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-cw'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'cofounder_welcome',
            {
                'user_first_name': '',
                'current_sprint_status_paragraph': '',
            },
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'welcome@aishippinglabs.com',
        )

    @override_settings(SES_FROM_EMAIL='noreply@aishippinglabs.com')
    @patch('email_app.services.email_service.boto3')
    def test_cofounder_welcome_from_studio_override_beats_legacy_noreply(
        self, mock_boto3,
    ):
        """Issue #950 grooming mitigation: when a stray legacy
        SES_FROM_EMAIL=noreply@ is present, a Studio (DB) override of
        SES_WELCOME_FROM_EMAIL=welcome@ pins the welcome From back to
        welcome@. This is the config-only guard the issue prescribes — a
        DB override counts as a runtime value even when it equals the code
        default, unlike a settings-level value.
        """
        IntegrationSetting.objects.create(
            key='SES_WELCOME_FROM_EMAIL',
            value='welcome@aishippinglabs.com',
            group='ses',
        )
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-cw2'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'cofounder_welcome',
            {
                'user_first_name': '',
                'current_sprint_status_paragraph': '',
            },
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'welcome@aishippinglabs.com',
        )

    @patch('email_app.services.email_service.boto3')
    def test_cofounder_welcome_from_is_welcome_without_legacy_override(
        self, mock_boto3,
    ):
        """Prod reality (per #950 grooming: infra injects NO SES_FROM_EMAIL):
        with no legacy override set, cofounder_welcome resolves From to the
        welcome@ default end-to-end through send() -> _send_ses().
        """
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-cw3'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'cofounder_welcome',
            {
                'user_first_name': '',
                'current_sprint_status_paragraph': '',
            },
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['FromEmailAddress'],
            'welcome@aishippinglabs.com',
        )

    # -- Issue #950: Reply-To on welcome emails ---------------------------

    @patch('email_app.services.email_service.boto3')
    def test_welcome_send_sets_default_reply_to(self, mock_boto3):
        """A welcome send sets ReplyToAddresses to the monitored inbox
        default when SES_WELCOME_REPLY_TO_EMAIL is not overridden.
        """
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-rt'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'cofounder_welcome',
            {
                'user_first_name': '',
                'current_sprint_status_paragraph': '',
            },
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['ReplyToAddresses'],
            ['welcome@aishippinglabs.com'],
        )

    @patch('email_app.services.email_service.boto3')
    def test_welcome_reply_to_honours_db_override(self, mock_boto3):
        """An IntegrationSetting override changes the welcome Reply-To
        address without a code change.
        """
        IntegrationSetting.objects.create(
            key='SES_WELCOME_REPLY_TO_EMAIL',
            value='team@aishippinglabs.com',
            group='ses',
        )
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-rt2'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'welcome',
            {'tier_name': 'Main'},
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(
            call_kwargs['ReplyToAddresses'],
            ['team@aishippinglabs.com'],
        )

    @patch('email_app.services.email_service.get_config')
    @patch('email_app.services.email_service.boto3')
    def test_welcome_reply_to_omitted_when_empty(self, mock_boto3, mock_get_config):
        """When the resolved SES_WELCOME_REPLY_TO_EMAIL is empty, the
        Reply-To header is omitted entirely (SES rejects an empty list).

        Note: an empty IntegrationSetting value cannot blank the welcome
        Reply-To because get_config falls through empty DB values to the
        code default. To exercise the omit branch we drive the resolved
        value to '' directly — the realistic way to disable Reply-To is to
        set the key's default empty in the registry, not via a blank
        override.
        """
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-rt3'}
        mock_boto3.client.return_value = mock_client

        def _cfg(key, default=''):
            if key == 'SES_WELCOME_REPLY_TO_EMAIL':
                return ''
            return default

        mock_get_config.side_effect = _cfg

        self.service._send_ses(
            'member@example.com',
            'Sub',
            '<html/>',
            email_type='welcome',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertNotIn('ReplyToAddresses', call_kwargs)

    @patch('email_app.services.email_service.boto3')
    def test_non_welcome_send_has_no_reply_to(self, mock_boto3):
        """Non-welcome (e.g. password_reset) emails carry no Reply-To —
        the monitored-inbox routing is welcome-only.
        """
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-rt4'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses(
            'to@example.com',
            'Sub',
            '<html/>',
            email_type='password_reset',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertNotIn('ReplyToAddresses', call_kwargs)

    # -- Issue #950: BCC plumbing -----------------------------------------

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_bcc_lands_in_destination(self, mock_boto3):
        """A bcc passed to _send_ses builds Destination.BccAddresses and
        leaves CcAddresses absent."""
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-bcc'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses(
            'member@example.com',
            'Sub',
            '<html/>',
            email_type='cofounder_welcome',
            bcc='staff@aishippinglabs.com',
        )

        call_kwargs = mock_client.send_email.call_args[1]
        destination = call_kwargs['Destination']
        self.assertEqual(destination['ToAddresses'], ['member@example.com'])
        self.assertEqual(
            destination['BccAddresses'],
            ['staff@aishippinglabs.com'],
        )
        self.assertNotIn('CcAddresses', destination)

    @patch('email_app.services.email_service.boto3')
    def test_send_threads_bcc_through_to_destination(self, mock_boto3):
        """EmailService.send(bcc=...) reaches the SES Destination."""
        clear_config_cache()
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-bcc2'}
        mock_boto3.client.return_value = mock_client

        self.service.send(
            self.user,
            'cofounder_welcome',
            {
                'user_first_name': '',
                'current_sprint_status_paragraph': '',
            },
            bcc='staff@aishippinglabs.com',
        )

        destination = mock_client.send_email.call_args[1]['Destination']
        self.assertEqual(
            destination['BccAddresses'],
            ['staff@aishippinglabs.com'],
        )
        self.assertNotIn('CcAddresses', destination)


class BuildUnsubscribeUrlTest(TestCase):
    """Regression test for issue #321: the unsubscribe link must use
    SITE_BASE_URL so dev/staging sends don't ship the prod hostname."""

    def setUp(self):
        self.user = User.objects.create_user(email='unsub@example.com')
        self.service = EmailService()

    @override_settings(SITE_BASE_URL='https://dev.aishippinglabs.com')
    def test_unsubscribe_url_uses_dev_site_base_url(self):
        url = self.service._build_unsubscribe_url(self.user)
        self.assertTrue(
            url.startswith('https://dev.aishippinglabs.com/api/unsubscribe?token='),
            msg=f'Expected dev host in unsubscribe URL, got: {url}',
        )
        self.assertNotIn('aishippinglabs.com/api/unsubscribe', url.replace(
            'dev.aishippinglabs.com', 'PLACEHOLDER',
        ))

    @override_settings(SITE_BASE_URL='http://localhost:8000')
    def test_unsubscribe_url_uses_localhost_site_base_url(self):
        url = self.service._build_unsubscribe_url(self.user)
        self.assertTrue(
            url.startswith('http://localhost:8000/api/unsubscribe?token='),
            msg=f'Expected localhost in unsubscribe URL, got: {url}',
        )


# ---------------------------------------------------------------------------
# Issue #450: footer "verify your email" CTA for unverified recipients
# ---------------------------------------------------------------------------


def _extract_verify_url_from_footer(html):
    """Pull the verify URL out of the rendered footer CTA paragraph.

    Returns ``None`` if the CTA is absent. Scoping the regex to the
    ``verify-email-cta`` paragraph guarantees we don't accidentally
    match the verify link a body template (e.g. ``email_verification_signup``)
    might also contain.
    """
    import re

    match = re.search(
        r'<p class="verify-email-cta">.*?<a href="([^"]+)"',
        html,
        re.DOTALL,
    )
    if match is None:
        return None
    return match.group(1)


@tag('core')
class VerifyEmailFooterTest(TestCase):
    """Issue #450: unverified recipients see a "verify your email" CTA
    in the footer above the unsubscribe link, on every transactional
    template except the explicitly opted-out ones."""

    def setUp(self):
        self.unverified = User.objects.create_user(
            email='unverified@example.com',
            first_name='Unv',
        )
        # email_verified defaults to False on User.create_user; assert
        # the precondition so a future model-default change cannot make
        # this test pass for the wrong reason.
        self.assertFalse(self.unverified.email_verified)

        self.verified = User.objects.create_user(
            email='verified@example.com',
            first_name='Ver',
            email_verified=True,
        )
        self.service = EmailService()

    @patch.object(EmailService, '_send_ses', return_value='ses-450-1')
    def test_unverified_recipient_email_contains_verify_cta(self, mock_ses):
        self.service.send(
            self.unverified, 'welcome', {'tier_name': 'Free'},
        )
        html = mock_ses.call_args[0][2]

        self.assertIn('<p class="verify-email-cta">', html)
        self.assertIn('Your email is not verified on our platform.', html)
        self.assertIn('click here', html)
        verify_url = _extract_verify_url_from_footer(html)
        self.assertIsNotNone(verify_url)
        self.assertIn('/api/verify-email?token=', verify_url)
        assert_no_internal_footer_text(self, html)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-2')
    def test_verified_recipient_email_omits_verify_cta(self, mock_ses):
        self.service.send(
            self.verified, 'welcome', {'tier_name': 'Free'},
        )
        html = mock_ses.call_args[0][2]

        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertIsNone(_extract_verify_url_from_footer(html))
        # Defensive: also confirm no token URL leaked anywhere on the
        # page (would indicate the body template embedded one we did
        # not intend to render for a verified user).
        self.assertNotIn('/api/verify-email?token=', html)
        assert_no_internal_footer_text(self, html)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-3a')
    def test_email_verification_signup_template_does_not_carry_verify_footer(
        self, mock_ses,
    ):
        # Issue #767: the signup-flow body has its own verify link via
        # ``verify_url``; the footer CTA must NOT duplicate it.
        self.service.send(
            self.unverified,
            'email_verification_signup',
            {
                'verify_url': 'https://example.test/api/verify-email?token=body',
                'site_url': 'https://example.test',
                'ttl_days': 7,
            },
        )
        html = mock_ses.call_args[0][2]

        # Body's own verify link is fine; footer CTA paragraph must not exist.
        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertIsNone(_extract_verify_url_from_footer(html))
        assert_no_internal_footer_text(self, html)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-3b')
    def test_email_verification_subscribe_template_does_not_carry_verify_footer(
        self, mock_ses,
    ):
        # Issue #767: same footer-suppression rule for the subscribe-flow
        # body. The body's confirm-subscription CTA must not be duplicated
        # by the footer.
        self.service.send(
            self.unverified,
            'email_verification_subscribe',
            {
                'verify_url': 'https://example.test/api/verify-email?token=body',
                'site_url': 'https://example.test',
                'ttl_days': 7,
            },
        )
        html = mock_ses.call_args[0][2]

        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertIsNone(_extract_verify_url_from_footer(html))
        assert_no_internal_footer_text(self, html)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-4')
    def test_password_reset_template_does_not_carry_verify_footer(
        self, mock_ses,
    ):
        self.service.send(
            self.unverified,
            'password_reset',
            {'reset_url': 'https://example.test/api/password-reset?token=x'},
        )
        html = mock_ses.call_args[0][2]

        self.assertNotIn('<p class="verify-email-cta">', html)
        self.assertIsNone(_extract_verify_url_from_footer(html))
        assert_no_internal_footer_text(self, html)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-5')
    def test_verify_cta_renders_without_transactional_unsubscribe(self, mock_ses):
        self.service.send(
            self.unverified, 'welcome', {'tier_name': 'Free'},
        )
        html = mock_ses.call_args[0][2]

        # Find the rendered CTA paragraph, not the CSS rule in <style>.
        verify_idx = html.find('<p class="verify-email-cta">')
        self.assertNotEqual(verify_idx, -1, 'verify CTA missing')
        self.assertNotIn('/api/unsubscribe?token=', html)
        assert_no_internal_footer_text(self, html)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-6')
    def test_verify_url_is_one_click_jwt_token(self, mock_ses):
        import jwt as _jwt

        self.service.send(
            self.unverified, 'welcome', {'tier_name': 'Free'},
        )
        html = mock_ses.call_args[0][2]

        verify_url = _extract_verify_url_from_footer(html)
        self.assertIsNotNone(verify_url)
        token = verify_url.split('token=', 1)[1]

        payload = _jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=['HS256'],
        )
        self.assertEqual(payload['action'], 'verify_email')
        self.assertEqual(payload['user_id'], self.unverified.pk)

    @patch.object(EmailService, '_send_ses', return_value='ses-450-7')
    def test_verify_url_token_expires_in_seven_days(self, mock_ses):
        import datetime

        import jwt as _jwt
        from freezegun import freeze_time

        with freeze_time('2026-05-01 12:00:00'):
            self.service.send(
                self.unverified, 'welcome', {'tier_name': 'Free'},
            )

        html = mock_ses.call_args[0][2]
        verify_url = _extract_verify_url_from_footer(html)
        token = verify_url.split('token=', 1)[1]

        # Decode without checking exp so we can assert the value itself.
        payload = _jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=['HS256'],
            options={'verify_exp': False},
        )
        expected_exp = datetime.datetime(
            2026, 5, 8, 12, 0, 0, tzinfo=datetime.timezone.utc,
        )
        actual_exp = datetime.datetime.fromtimestamp(
            payload['exp'], tz=datetime.timezone.utc,
        )
        # 168 hours == 7 days from frozen ``now``.
        self.assertEqual(actual_exp, expected_exp)

    def test_send_does_not_mint_token_for_verified_user(self):
        # Patch the helper at its definition site so any import path the
        # service uses goes through the mock.
        with (
            patch.object(EmailService, '_send_ses', return_value='ses-450-8'),
            patch(
                'accounts.views.auth._generate_verification_token',
            ) as mock_mint,
        ):
            self.service.send(
                self.verified, 'welcome', {'tier_name': 'Free'},
            )
        mock_mint.assert_not_called()

    @patch.object(EmailService, '_send_ses', return_value='ses-450-9')
    def test_render_html_email_passes_verify_url_to_template(self, mock_ses):
        # Direct unit test of the keyword-arg plumbing: when the caller
        # passes ``verify_email_url`` to ``render_html_email`` the wrapped
        # HTML must contain the CTA paragraph and the URL itself.
        html = self.service.render_html_email(
            'Subject',
            '<p>body</p>',
            unsubscribe_url='https://example.test/api/unsubscribe?token=u',
            verify_email_url='https://example.test/api/verify-email?token=v',
        )
        self.assertIn('<p class="verify-email-cta">', html)
        self.assertIn(
            'https://example.test/api/verify-email?token=v', html,
        )
        assert_no_internal_footer_text(self, html)

    def test_render_html_email_omits_cta_when_url_none(self):
        html = self.service.render_html_email(
            'Subject',
            '<p>body</p>',
            unsubscribe_url='https://example.test/api/unsubscribe?token=u',
            verify_email_url=None,
        )
        self.assertNotIn('<p class="verify-email-cta">', html)
        assert_no_internal_footer_text(self, html)
