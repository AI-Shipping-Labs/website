"""Source-derived plain-text alternatives for application email (#1567)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from content.utils.markdown import render_email_plain_text
from email_app.models import (
    CampaignDelivery,
    EmailCampaign,
    EmailLog,
    EmailTemplateOverride,
)
from email_app.services.email_service import EmailService
from email_app.tasks.send_campaign import send_campaign_batch

User = get_user_model()


@tag('core')
class EmailPlainTextMarkdownTest(TestCase):
    def test_source_markdown_keeps_structure_code_unicode_and_destinations(self):
        source = """\
# Café 🚀

A **bold** and _italic_ paragraph with `inline()`.

1. First
2. [Docs](https://example.test/docs)

- Bare https://example.test/bare
- [https://example.test/self](https://example.test/self)
- [Email us](mailto:hello@example.test)

```python
print("héllo")
```

<div>Raw <b>HTML</b></div>
"""

        plain_text = render_email_plain_text(source)

        self.assertIn('Café 🚀\n\nA bold and italic paragraph', plain_text)
        self.assertIn('1. First\n2. Docs (https://example.test/docs)', plain_text)
        self.assertIn('- Bare https://example.test/bare', plain_text)
        self.assertEqual(plain_text.count('https://example.test/self'), 1)
        self.assertIn('Email us (mailto:hello@example.test)', plain_text)
        self.assertIn('print("héllo")', plain_text)
        self.assertIn('Raw HTML', plain_text)
        self.assertNotIn('<div>', plain_text)
        self.assertNotIn('**', plain_text)


@tag('core')
@override_settings(SITE_BASE_URL='https://example.test')
class EmailPlainTextPreparationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='alice@example.test',
            first_name='Alice',
            email_verified=False,
        )

    def setUp(self):
        self.service = EmailService()

    @patch.object(
        EmailService,
        '_build_verify_email_url',
        return_value='https://example.test/api/verify-email?token=fixed',
    )
    def test_filesystem_and_identical_override_prepare_the_same_two_parts(
        self, _verify_url,
    ):
        subject_source, body_source, _footer = self.service._load_template_source(
            'welcome',
        )
        filesystem = self.service.prepare_template(
            self.user, 'welcome', {'tier_name': 'Main'},
        )
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject=subject_source,
            body_markdown=body_source,
        )

        overridden = self.service.prepare_template(
            self.user, 'welcome', {'tier_name': 'Main'},
        )

        self.assertEqual(overridden.subject, filesystem.subject)
        self.assertEqual(overridden.full_html, filesystem.full_html)
        self.assertEqual(overridden.plain_text, filesystem.plain_text)
        for expected in (
            'Alice',
            'https://example.test/community/slack',
            'https://example.test/api/verify-email?token=fixed',
        ):
            self.assertIn(expected, filesystem.full_html)
            self.assertIn(expected, filesystem.plain_text)
        self.assertNotIn('{{', filesystem.plain_text)
        self.assertNotIn('{%', filesystem.plain_text)

    def test_optional_footer_actions_have_plain_and_html_parity(self):
        cases = (
            ({}, (False, False, False)),
            ({'footer_note': 'Private preview note'}, (True, False, False)),
            (
                {'verify_email_url': 'https://example.test/verify'},
                (False, True, False),
            ),
            (
                {'unsubscribe_url': 'https://example.test/unsubscribe'},
                (False, False, True),
            ),
        )
        needles = (
            'Private preview note',
            'https://example.test/verify',
            'https://example.test/unsubscribe',
        )
        for kwargs, expected_presence in cases:
            with self.subTest(kwargs=kwargs):
                html = self.service.render_html_email('Subject', '<p>Body</p>', **kwargs)
                plain_text = self.service.render_plain_text_email('Body', **kwargs)
                for needle, should_exist in zip(
                    needles, expected_presence, strict=True,
                ):
                    self.assertEqual(needle in html, should_exist)
                    self.assertEqual(needle in plain_text, should_exist)

    @patch.object(EmailService, '_send_ses', return_value='maven-ses-id')
    def test_maven_welcome_plain_text_keeps_every_action_url(self, mock_ses):
        urls = {
            'sign_in_url': 'https://example.test/accounts/login/',
            'password_reset_url': 'https://example.test/accounts/password/reset/',
            'slack_join_url': 'https://example.test/community/slack',
            'onboarding_url': 'https://example.test/onboarding/',
            'newsletter_opt_in_url': (
                'https://example.test/api/verify-and-subscribe?token=newsletter'
            ),
            'opt_out_url': (
                'https://example.test/api/maven-email-opt-out?token=course'
            ),
        }

        self.service.send(
            self.user,
            'maven_welcome',
            {**urls, 'course_name': 'AI Builders', 'course_channel': '#builders'},
        )

        plain_text = mock_ses.call_args.kwargs['text_body']
        for url in urls.values():
            self.assertIn(url, plain_text)
        self.assertNotIn('{{', plain_text)
        self.assertNotIn('{%', plain_text)

    @patch.object(
        EmailService,
        '_build_unsubscribe_url',
        return_value='https://example.test/unsubscribe?token=member',
    )
    @patch.object(
        EmailService,
        '_build_verify_email_url',
        return_value='https://example.test/verify?token=member',
    )
    def test_campaign_preparation_derives_both_parts_and_actions_from_markdown(
        self, _verify_url, _unsubscribe_url,
    ):
        body = '# Update\n\n- First\n- [Take action](https://example.test/action)'

        prepared = self.service.prepare_rendered(
            self.user, 'Campaign subject', body, email_type='campaign',
        )

        self.assertIn('<h1>Update</h1>', prepared.full_html)
        self.assertIn('Update\n\n- First', prepared.plain_text)
        for url in (
            'https://example.test/action',
            'https://example.test/verify?token=member',
            'https://example.test/unsubscribe?token=member',
        ):
            self.assertIn(url, prepared.full_html)
            self.assertIn(url, prepared.plain_text)


@tag('core')
@override_settings(SITE_BASE_URL='https://example.test')
class CampaignPlainTextDeliveryTest(TestCase):
    @patch.object(EmailService, '_send_ses', return_value='campaign-ses-id')
    def test_confirmed_delivery_carries_both_parts_and_keeps_one_log(self, mock_ses):
        user = User.objects.create_user(
            email='campaign@example.test',
            email_verified=True,
            unsubscribed=False,
        )
        campaign = EmailCampaign.objects.create(
            subject='Plain campaign',
            body=(
                '# Shipping update\n\n- One\n- Two\n\n'
                '[Read it](https://example.test/read)'
            ),
            status='sending',
            audience_snapshotted_at=timezone.now(),
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
        )

        result = send_campaign_batch(
            campaign.pk, [delivery.pk], send_delay=0,
        )

        self.assertEqual(result['sent_count'], 1)
        html = mock_ses.call_args.args[2]
        plain_text = mock_ses.call_args.kwargs['text_body']
        self.assertIn('<h1>Shipping update</h1>', html)
        self.assertIn('Shipping update\n\n- One\n- Two', plain_text)
        self.assertIn('https://example.test/read', html)
        self.assertIn('Read it (https://example.test/read)', plain_text)
        unsubscribe_url = mock_ses.call_args.kwargs['unsubscribe_url']
        self.assertIn(unsubscribe_url, html)
        self.assertIn(unsubscribe_url, plain_text)

        campaign.refresh_from_db()
        delivery.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(delivery.ses_message_id, 'campaign-ses-id')
        logs = EmailLog.objects.filter(campaign=campaign, user=user)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().recipient_email, user.email)
        self.assertEqual(logs.get().subject, campaign.subject)
