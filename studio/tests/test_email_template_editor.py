"""Studio email template editor (issue #455).

Covers the list page, the edit form (GET prefill + POST upsert), the
reset-to-default endpoint, the live-preview endpoint, and the
send-test-to-me action. Access control is included in this same file
because the editor is a single feature surface.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from email_app.models import EmailTemplateOverride
from email_app.services.email_service import EmailService

User = get_user_model()


class EmailTemplateAccessTest(TestCase):
    """Only authenticated staff can reach any of the endpoints."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', is_staff=False,
        )

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get('/studio/email-templates/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_gets_403(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/email-templates/')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_post_to_edit_does_not_create_override(self):
        # Side-effect verification per Rule 12.
        before = EmailTemplateOverride.objects.count()
        response = self.client.post(
            '/studio/email-templates/welcome/edit/',
            {
                'subject': 'X',
                'body_markdown': 'Y',
                'footer_note': 'Z',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertEqual(EmailTemplateOverride.objects.count(), before)

    def test_non_staff_post_to_reset_does_not_delete_override(self):
        EmailTemplateOverride.objects.create(
            template_name='welcome', subject='S', body_markdown='B',
        )
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(
            '/studio/email-templates/welcome/reset/',
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            EmailTemplateOverride.objects.filter(
                template_name='welcome',
            ).exists(),
        )


class EmailTemplateListTest(TestCase):
    """The list page surfaces every transactional template."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def test_list_shows_all_ten_templates(self):
        response = self.client.get('/studio/email-templates/')
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        names = {r['template_name'] for r in rows}
        for expected in [
            'welcome',
            'email_verification',
            'password_reset',
            'community_invite',
            'lead_magnet_delivery',
            'event_registration',
            'event_reminder',
            'cancellation',
            'payment_failed',
            'welcome_imported',
        ]:
            self.assertIn(expected, names)
        self.assertGreaterEqual(len(rows), 10)

    def test_list_shows_edited_indicator_for_overridden_templates(self):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Custom subject',
            body_markdown='Custom body',
        )
        response = self.client.get('/studio/email-templates/')
        rows_by_name = {r['template_name']: r for r in response.context['rows']}
        self.assertTrue(rows_by_name['welcome']['edited'])
        self.assertEqual(rows_by_name['welcome']['subject'], 'Custom subject')
        self.assertIsNotNone(rows_by_name['welcome']['updated_at'])

        # A template without an override should not show the badge.
        self.assertFalse(rows_by_name['email_verification']['edited'])
        self.assertIsNone(rows_by_name['email_verification']['updated_at'])

    def test_list_uses_file_subject_when_no_override(self):
        response = self.client.get('/studio/email-templates/')
        rows_by_name = {r['template_name']: r for r in response.context['rows']}
        # ``welcome.md`` has subject "Welcome to {{ tier_name }}!"
        self.assertIn(
            '{{ tier_name }}',
            rows_by_name['welcome']['subject'],
        )

    def test_sidebar_links_to_email_templates(self):
        response = self.client.get('/studio/')
        self.assertContains(
            response, 'href="/studio/email-templates/"',
        )


class EmailTemplateEditGetTest(TestCase):
    """GET prefills the form from override or file."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def test_unknown_template_returns_404(self):
        response = self.client.get(
            '/studio/email-templates/no-such-template/edit/',
        )
        self.assertEqual(response.status_code, 404)

    def test_edit_form_prefills_from_file_when_no_override(self):
        response = self.client.get(
            '/studio/email-templates/welcome/edit/',
        )
        self.assertEqual(response.status_code, 200)
        initial = response.context['initial']
        self.assertFalse(initial['has_override'])
        # File body content (post-frontmatter).
        self.assertIn('Browse our', initial['body_markdown'])
        # The on-disk subject template is the YAML frontmatter value.
        self.assertEqual(
            initial['subject'], 'Welcome to {{ tier_name }}!',
        )
        self.assertEqual(initial['footer_note'], '')

    def test_edit_form_prefills_from_override_when_present(self):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='OVR subject',
            body_markdown='OVR body',
            footer_note='OVR footer',
        )
        response = self.client.get(
            '/studio/email-templates/welcome/edit/',
        )
        initial = response.context['initial']
        self.assertTrue(initial['has_override'])
        self.assertEqual(initial['subject'], 'OVR subject')
        self.assertEqual(initial['body_markdown'], 'OVR body')
        self.assertEqual(initial['footer_note'], 'OVR footer')
        # The file body must NOT leak into the form when an override exists.
        self.assertNotIn('Browse our', initial['body_markdown'])


class EmailTemplateEditPostTest(TestCase):
    """POST upserts the override row and redirects to the list."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def test_post_creates_override_row(self):
        response = self.client.post(
            '/studio/email-templates/welcome/edit/',
            {
                'subject': 'X',
                'body_markdown': 'Y',
                'footer_note': 'Z',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/email-templates/')
        override = EmailTemplateOverride.objects.get(template_name='welcome')
        self.assertEqual(override.subject, 'X')
        self.assertEqual(override.body_markdown, 'Y')
        self.assertEqual(override.footer_note, 'Z')
        self.assertEqual(override.updated_by, self.staff)

    def test_post_updates_existing_override_without_creating_new_row(self):
        existing = EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Original',
            body_markdown='Original body',
        )
        original_pk = existing.pk
        original_updated_at = existing.updated_at

        self.client.post(
            '/studio/email-templates/welcome/edit/',
            {
                'subject': 'Updated',
                'body_markdown': 'Updated body',
                'footer_note': 'Updated footer',
            },
        )

        self.assertEqual(EmailTemplateOverride.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.pk, original_pk)
        self.assertEqual(existing.subject, 'Updated')
        self.assertEqual(existing.body_markdown, 'Updated body')
        self.assertEqual(existing.footer_note, 'Updated footer')
        self.assertGreater(existing.updated_at, original_updated_at)
        self.assertEqual(existing.updated_by, self.staff)

    def test_post_with_blank_subject_does_not_create_override(self):
        response = self.client.post(
            '/studio/email-templates/welcome/edit/',
            {
                'subject': '   ',
                'body_markdown': 'Y',
                'footer_note': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EmailTemplateOverride.objects.filter(
                template_name='welcome',
            ).exists(),
        )

    def test_post_with_blank_body_does_not_create_override(self):
        response = self.client.post(
            '/studio/email-templates/welcome/edit/',
            {
                'subject': 'X',
                'body_markdown': '   ',
                'footer_note': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EmailTemplateOverride.objects.filter(
                template_name='welcome',
            ).exists(),
        )


class EmailTemplateResetTest(TestCase):
    """POST to /reset/ deletes the override row."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def test_reset_deletes_override(self):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='S',
            body_markdown='B',
        )
        response = self.client.post(
            '/studio/email-templates/welcome/reset/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/email-templates/')
        self.assertFalse(
            EmailTemplateOverride.objects.filter(
                template_name='welcome',
            ).exists(),
        )

    def test_reset_when_no_override_is_a_noop(self):
        response = self.client.post(
            '/studio/email-templates/welcome/reset/',
        )
        # Still redirects to the list; no row created or deleted.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailTemplateOverride.objects.count(), 0)

    def test_reset_get_returns_405(self):
        response = self.client.get(
            '/studio/email-templates/welcome/reset/',
        )
        self.assertEqual(response.status_code, 405)


class EmailTemplatePreviewTest(TestCase):
    """The preview endpoint returns the chrome-wrapped HTML."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    def test_preview_renders_markdown_to_html(self):
        response = self.client.post(
            '/studio/email-templates/welcome/preview/',
            {
                'subject': 'Sub',
                'body_markdown': '**hello**',
                'footer_note': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('<strong>hello</strong>', body)

    def test_preview_wraps_in_chrome(self):
        response = self.client.post(
            '/studio/email-templates/welcome/preview/',
            {
                'subject': 'Sub',
                'body_markdown': 'Body',
                'footer_note': '',
            },
        )
        body = response.content.decode()
        # Chrome markers from base_email.html:
        self.assertIn('email-footer', body)
        self.assertIn('email-header', body)

    def test_preview_substitutes_placeholder_user_name(self):
        response = self.client.post(
            '/studio/email-templates/welcome/preview/',
            {
                'subject': 'Sub',
                'body_markdown': 'Hi {{ user_name }},',
                'footer_note': '',
            },
        )
        body = response.content.decode()
        # ``user_name`` defaults to "Ada" in PREVIEW_CONTEXTS for welcome.
        self.assertIn('Hi Ada,', body)
        # And the literal Django variable should NOT appear unrendered.
        self.assertNotIn('{{ user_name }}', body)

    def test_preview_includes_footer_note_when_provided(self):
        response = self.client.post(
            '/studio/email-templates/welcome/preview/',
            {
                'subject': 'Sub',
                'body_markdown': 'Body',
                'footer_note': 'P.S. preview footer',
            },
        )
        body = response.content.decode()
        footer_idx = body.index('email-footer')
        self.assertIn('P.S. preview footer', body[footer_idx:])

    def test_preview_unknown_template_returns_404(self):
        response = self.client.post(
            '/studio/email-templates/no-such/preview/',
            {'subject': 'a', 'body_markdown': 'b', 'footer_note': ''},
        )
        self.assertEqual(response.status_code, 404)

    def test_preview_get_returns_405(self):
        response = self.client.get(
            '/studio/email-templates/welcome/preview/',
        )
        self.assertEqual(response.status_code, 405)


class EmailTemplateSendTestTest(TestCase):
    """Send-test fires a real send with the persisted state."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
            first_name='Operator',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pw')

    @patch.object(EmailService, '_send_ses', return_value='ses-test-1')
    def test_send_test_uses_override_body_when_present(self, mock_ses):
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='OVR subject',
            body_markdown='OVR test body for {{ user_name }}',
        )

        response = self.client.post(
            '/studio/email-templates/welcome/send-test/',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/email-templates/')
        mock_ses.assert_called_once()
        to_email, subject, html_body = mock_ses.call_args[0]
        self.assertEqual(to_email, 'staff@test.com')
        self.assertEqual(subject, 'OVR subject')
        self.assertIn('OVR test body', html_body)
        self.assertIn('Operator', html_body)

    @patch.object(EmailService, '_send_ses', return_value='ses-test-2')
    def test_send_test_uses_file_when_no_override(self, mock_ses):
        response = self.client.post(
            '/studio/email-templates/welcome/send-test/',
        )

        self.assertEqual(response.status_code, 302)
        mock_ses.assert_called_once()
        html_body = mock_ses.call_args[0][2]
        # Filesystem template body fragment.
        self.assertIn('Browse our', html_body)

    def test_send_test_unknown_template_returns_404(self):
        response = self.client.post(
            '/studio/email-templates/no-such/send-test/',
        )
        self.assertEqual(response.status_code, 404)

    def test_send_test_get_returns_405(self):
        response = self.client.get(
            '/studio/email-templates/welcome/send-test/',
        )
        self.assertEqual(response.status_code, 405)
