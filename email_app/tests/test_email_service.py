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

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from email_app.models import EmailLog
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
        self.user = User.objects.create_user(
            email='alice@example.com',
            first_name='Alice',
        )
        self.service = EmailService()

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
        args = mock_ses.call_args
        self.assertEqual(args[0][0], 'alice@example.com')  # to_email
        self.assertIn('Welcome to Basic', args[0][1])  # subject

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-003')
    def test_send_skips_unsubscribed_user(self, mock_ses):
        self.user.unsubscribed = True
        self.user.save()

        result = self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        self.assertIsNone(result)
        mock_ses.assert_not_called()
        self.assertEqual(EmailLog.objects.count(), 0)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-004')
    def test_send_renders_user_name_in_body(self, mock_ses):
        self.service.send(self.user, 'welcome', {'tier_name': 'Premium'})

        call_args = mock_ses.call_args
        html_body = call_args[0][2]  # full HTML
        self.assertIn('Alice', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-005')
    def test_send_renders_user_email_fallback_name(self, mock_ses):
        user = User.objects.create_user(email='bob@example.com')
        self.service.send(user, 'welcome', {'tier_name': 'Free'})

        call_args = mock_ses.call_args
        html_body = call_args[0][2]
        self.assertIn('bob', html_body)

    def test_send_invalid_template_raises_error(self):
        with self.assertRaises(EmailServiceError) as ctx:
            self.service.send(self.user, 'nonexistent_template', {})
        self.assertIn('not found', str(ctx.exception))

    @patch.object(EmailService, '_send_ses', return_value='ses-msg-006')
    def test_send_includes_unsubscribe_link(self, mock_ses):
        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        call_args = mock_ses.call_args
        html_body = call_args[0][2]
        self.assertIn('Unsubscribe', html_body)
        self.assertIn('/api/unsubscribe?token=', html_body)

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
            'slack_invite_url': 'https://slack.com/invite',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('Welcome to Main', subject)
        self.assertIn('Tester', html)
        self.assertIn('slack.com/invite', html)

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
        self.service.send(self.user, 'community_invite', {
            'slack_invite_url': 'https://slack.com/join/abc',
        })
        call_args = mock_ses.call_args
        subject = call_args[0][1]
        html = call_args[0][2]
        self.assertIn('community', subject)
        self.assertIn('slack.com/join/abc', html)

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


class EmailServiceSESIntegrationTest(TestCase):
    """Test SES API integration (mocked)."""

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
        mock_client.send_email.side_effect = Exception('SES down')
        mock_boto3.client.return_value = mock_client

        with (
            self.assertLogs('email_app.services.email_service', level='ERROR') as logs,
            self.assertRaises(EmailServiceError) as ctx,
        ):
            self.service._send_ses('to@example.com', 'Sub', '<html/>')
        self.assertIn('Failed to send email via SES to to@example.com', logs.output[0])
        self.assertIn('SES send failed', str(ctx.exception))

    @override_settings(SES_FROM_EMAIL='custom@example.com')
    @patch('email_app.services.email_service.boto3')
    def test_send_ses_uses_configured_from_email(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses('to@example.com', 'Sub', '<html/>')

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['FromEmailAddress'], 'custom@example.com')

    @patch('email_app.services.email_service.boto3')
    def test_send_ses_uses_integration_setting_from_email(self, mock_boto3):
        IntegrationSetting.objects.create(
            key='SES_FROM_EMAIL',
            value='sender@example.com',
            group='ses',
        )
        clear_config_cache()

        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'id-123'}
        mock_boto3.client.return_value = mock_client

        self.service._send_ses('to@example.com', 'Sub', '<html/>')

        call_kwargs = mock_client.send_email.call_args[1]
        self.assertEqual(call_kwargs['FromEmailAddress'], 'sender@example.com')

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
    match the verify link a body template (e.g. ``email_verification``)
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
        self.assertIn('Verify your email', html)
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

    @patch.object(EmailService, '_send_ses', return_value='ses-450-3')
    def test_email_verification_template_does_not_carry_verify_footer(
        self, mock_ses,
    ):
        # The body has its own verify link via ``verify_url``; the footer
        # CTA must NOT appear on top of that.
        self.service.send(
            self.unverified,
            'email_verification',
            {'verify_url': 'https://example.test/api/verify-email?token=body'},
        )
        html = mock_ses.call_args[0][2]

        # Body's own verify link is fine; footer CTA paragraph must not exist.
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
    def test_verify_cta_is_above_unsubscribe_in_rendered_html(self, mock_ses):
        self.service.send(
            self.unverified, 'welcome', {'tier_name': 'Free'},
        )
        html = mock_ses.call_args[0][2]

        # Find the rendered CTA paragraph, not the CSS rule in <style>.
        verify_idx = html.find('<p class="verify-email-cta">')
        unsub_idx = html.find('/api/unsubscribe?token=')

        self.assertNotEqual(verify_idx, -1, 'verify CTA missing')
        self.assertNotEqual(unsub_idx, -1, 'unsubscribe link missing')
        self.assertLess(
            verify_idx,
            unsub_idx,
            'verify CTA must appear before the unsubscribe link in DOM order',
        )
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
