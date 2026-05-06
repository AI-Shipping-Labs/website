"""EmailService picks DB overrides over filesystem templates (issue #455).

The override is purely additive: when no row exists, behaviour is
identical to the pre-#455 file-only render path. The tests that prove
that fall-back live in ``test_email_service.py``; here we cover the
override-present cases.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.models import EmailTemplateOverride
from email_app.services.email_service import EmailService

User = get_user_model()


class EmailServiceOverridePrecedenceTest(TestCase):
    """``EmailService.send`` must use the override row when one exists."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='alice@example.com',
            first_name='Alice',
        )

    def setUp(self):
        self.service = EmailService()

    @patch.object(EmailService, '_send_ses', return_value='ses-001')
    def test_send_uses_override_body_when_present(self, mock_ses):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Welcome to {{ tier_name }}!',
            body_markdown='OVERRIDE BODY for {{ user_name }}',
        )

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        # SES is called: (to_email, subject, html_body)
        args = mock_ses.call_args[0]
        html_body = args[2]
        self.assertIn('OVERRIDE BODY', html_body)
        self.assertIn('Alice', html_body)  # variable substitution still happens
        # The original file body should not leak through.
        self.assertNotIn('Welcome to **AI Shipping Labs**', html_body)
        self.assertNotIn('Browse our', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-002')
    def test_send_falls_back_to_file_when_no_override(self, mock_ses):
        # No override row: guarantee using the filesystem template.
        self.assertFalse(EmailTemplateOverride.objects.exists())

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        html_body = mock_ses.call_args[0][2]
        # File contents (welcome.md) include this exact phrase.
        self.assertIn('Browse our', html_body)
        self.assertNotIn('OVERRIDE BODY', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-003')
    def test_send_uses_override_subject(self, mock_ses):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='OVERRIDE SUBJECT',
            body_markdown='Body for {{ user_name }}',
        )

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        subject = mock_ses.call_args[0][1]
        self.assertEqual(subject, 'OVERRIDE SUBJECT')

    @patch.object(EmailService, '_send_ses', return_value='ses-004')
    def test_override_footer_note_appears_in_chrome(self, mock_ses):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Subject',
            body_markdown='Body',
            footer_note='P.S. limited time',
        )

        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        html_body = mock_ses.call_args[0][2]
        # The chrome's footer block contains the email-footer class; the
        # P.S. text must appear inside that block, not just somewhere on
        # the page.
        # Slice from the footer marker forward to verify placement.
        footer_idx = html_body.index('email-footer')
        footer_segment = html_body[footer_idx:]
        self.assertIn('P.S. limited time', footer_segment)

    @patch.object(EmailService, '_send_ses', return_value='ses-005')
    def test_no_footer_note_for_file_template(self, mock_ses):
        # When using the file (no override), footer_note should be empty
        # so the chrome's ``{% if footer_note %}`` block is not rendered.
        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})

        html_body = mock_ses.call_args[0][2]
        self.assertNotIn('P.S. limited time', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-006')
    def test_override_deleted_falls_back_to_file(self, mock_ses):
        # First send with override, then delete and send again -- second
        # send must use the file content.
        override = EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='OVR',
            body_markdown='OVR BODY',
        )
        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})
        first_html = mock_ses.call_args[0][2]
        self.assertIn('OVR BODY', first_html)

        override.delete()
        self.service.send(self.user, 'welcome', {'tier_name': 'Main'})
        second_html = mock_ses.call_args[0][2]
        self.assertIn('Browse our', second_html)
        self.assertNotIn('OVR BODY', second_html)
