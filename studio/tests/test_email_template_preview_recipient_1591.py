"""Issue #1591: Studio preview recipient selector.

The preview pane hardcoded ``Ada``, so nobody reviewing copy could see the
greeting that a member with no name on file actually receives.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from email_app.models import EmailTemplateOverride
from email_app.services.email_service import EmailService

User = get_user_model()


class PreviewRecipientSelectorMarkupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def test_edit_page_renders_the_recipient_select(self):
        response = self.client.get('/studio/email-templates/welcome/edit/')

        self.assertContains(response, 'data-testid="preview-recipient"')
        self.assertContains(response, '<option value="named" selected>')
        self.assertContains(response, '<option value="no_name">')
        self.assertContains(response, 'No name on file')
        self.assertContains(
            response, 'Roughly 7 in 10 accounts have no name on file.',
        )

    def test_select_carries_the_studio_select_chrome_class(self):
        response = self.client.get('/studio/email-templates/welcome/edit/')
        self.assertIn('studio-select', response.content.decode())

    def test_select_is_wired_to_the_preview_refresh(self):
        """It must be a ``data-preview-field`` so ``gather()`` posts it."""
        response = self.client.get('/studio/email-templates/welcome/edit/')
        self.assertIn(
            'data-preview-field="recipient"', response.content.decode(),
        )


class PreviewRecipientEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def _preview(self, template='welcome', body='Hi {{ user_name }},', **extra):
        payload = {'subject': 'Sub', 'body_markdown': body, 'footer_note': ''}
        payload.update(extra)
        response = self.client.post(
            f'/studio/email-templates/{template}/preview/', payload,
        )
        # The endpoint's contract is HTML for the iframe srcdoc.
        self.assertEqual(response['Content-Type'], 'text/html')
        body = response.content.decode()
        self.assertNotIn('Preview failed to render', body)
        return body

    def test_no_name_recipient_renders_hi_there(self):
        body = self._preview(recipient='no_name')
        self.assertIn('Hi there,', body)
        self.assertNotIn('Hi Ada,', body)

    def test_named_recipient_renders_hi_ada(self):
        body = self._preview(recipient='named')
        self.assertIn('Hi Ada,', body)
        self.assertNotIn('Hi there,', body)

    def test_omitting_the_recipient_keeps_the_named_preview(self):
        body = self._preview()
        self.assertIn('Hi Ada,', body)

    def test_unknown_recipient_falls_back_to_named(self):
        body = self._preview(recipient='wat')
        self.assertIn('Hi Ada,', body)
        self.assertNotIn('Preview failed to render', body)

    def test_blank_recipient_falls_back_to_named(self):
        body = self._preview(recipient='')
        self.assertIn('Hi Ada,', body)

    def test_no_name_preview_never_shows_a_real_looking_address(self):
        body = self._preview(
            body='Hi {{ user_name }},\n\nSent to {{ user_email }}.',
            recipient='no_name',
        )
        self.assertIn('Hi there,', body)
        self.assertIn('no-name@example.com', body)
        self.assertNotIn('ada@example.com', body)

    def test_only_the_greeting_differs_between_the_two_previews(self):
        markdown = 'Hi {{ user_name }},\n\nThe rest of the copy is fixed.\n'
        named = self._preview(body=markdown, recipient='named')
        degraded = self._preview(body=markdown, recipient='no_name')

        self.assertNotEqual(named, degraded)
        self.assertEqual(
            named.replace('Hi Ada,', 'GREETING'),
            degraded.replace('Hi there,', 'GREETING'),
        )

    def test_member_name_greeting_template_also_degrades(self):
        body = self._preview(
            template='sprint_partner_intro',
            body=(
                'Hi {{ member_name }},\n\n'
                '{% for partner in partners %}- {{ partner.name }}\n'
                '{% endfor %}'
            ),
            recipient='no_name',
        )
        self.assertIn('Hi there,', body)
        self.assertIn('Grace Hopper', body)

    def test_operator_override_body_also_degrades(self):
        """The override path is the whole reason the fallback is resolved in
        code rather than as ``|default:"there"`` in the template file."""
        EmailTemplateOverride.objects.create(
            template_name='password_reset',
            subject='Reset',
            body_markdown='Hello {{ user_name }},\n',
        )

        response = self.client.get(
            '/studio/email-templates/password_reset/edit/',
        )
        self.assertIn('Hello {{ user_name }},', response.content.decode())

        body = self._preview(
            template='password_reset',
            body='Hello {{ user_name }},',
            recipient='no_name',
        )
        self.assertIn('Hello there,', body)


class SendTestUnaffectedByPreviewRecipientTest(TestCase):
    """The preview selector is preview-only: a real send must still address
    the operator by their own name."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='operator@test.com',
            password='pw',
            is_staff=True,
            first_name='Grace',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='operator@test.com', password='pw')

    @patch.object(EmailService, '_send_ses', return_value='ses-1591')
    def test_send_test_uses_the_operator_real_name(self, mock_ses):
        response = self.client.post(
            '/studio/email-templates/welcome/send-test/',
            {'recipient': 'no_name'},
        )

        self.assertEqual(response['Location'], '/studio/email-templates/')
        self.assertEqual(mock_ses.call_count, 1)
        to_email, _subject, html_body = mock_ses.call_args[0]
        self.assertEqual(to_email, 'operator@test.com')
        self.assertIn('Hi Grace,', html_body)
        self.assertNotIn('Hi there,', html_body)
        self.assertNotIn('Hi Ada,', html_body)
