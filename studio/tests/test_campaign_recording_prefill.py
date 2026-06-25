"""Studio tests for the recording-available campaign pre-fill flow (#1076).

Covers the deep-link ``/studio/campaigns/new?event=<id>&template=
recording_available`` that the host recording-ready email and the Studio
event page link to: it pre-selects the event audience and pre-fills the
subject/body, and saving persists ``target_event`` without sending.
"""

from datetime import date, datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from content.models import Workshop
from email_app.models import EmailCampaign, EmailLog
from email_app.services.recording_available_prefill import (
    build_recording_available_prefill,
)
from events.models import Event, EventRegistration
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()
UTC = dt_timezone.utc


@tag('core')
class RecordingPrefillViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Shipping Agents Workshop',
            slug='shipping-agents-workshop',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=agents',
        )
        cls.workshop = Workshop.objects.create(
            slug='shipping-agents',
            title='Shipping Agents',
            date=date(2026, 6, 8),
            description='Here is the **full write-up** of the workshop.',
            event=cls.event,
        )
        for i in range(3):
            user = User.objects.create_user(
                email=f'reg{i}@test.com', email_verified=True,
            )
            EventRegistration.objects.create(event=cls.event, user=user)

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
            email_verified=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    def _prefill_url(self, event=None):
        return (
            f"{reverse('studio_campaign_create')}"
            f"?event={(event or self.event).pk}&template=recording_available"
        )

    def test_prefill_preselects_event_and_fills_body(self):
        response = self.client.get(self._prefill_url())
        self.assertEqual(response.status_code, 200)
        # Event is pre-selected as the audience.
        self.assertEqual(
            response.context['selected_event_id'], self.event.pk,
        )
        # Subject + body pre-filled, including the workshop write-up text.
        self.assertContains(response, 'Shipping Agents Workshop')
        self.assertContains(response, 'full write-up')

    def test_prefill_recipient_count_is_registrant_count(self):
        response = self.client.get(self._prefill_url())
        # 3 registrants, not the whole subscriber base.
        self.assertEqual(response.context['recipient_count'], 3)

    def test_save_persists_target_event_without_sending(self):
        prefill = build_recording_available_prefill(self.event)
        response = self.client.post(reverse('studio_campaign_create'), {
            'subject': prefill['subject'],
            'body': prefill['body'],
            'target_min_level': '0',
            'target_event': str(self.event.pk),
            'slack_filter': 'any',
            'audience_verification': 'verified_only',
        })
        self.assertEqual(response.status_code, 302)
        campaign = EmailCampaign.objects.get(subject=prefill['subject'])
        self.assertEqual(campaign.target_event_id, self.event.pk)
        self.assertEqual(campaign.status, 'draft')
        # Nothing was sent by opening/saving the draft.
        self.assertFalse(EmailLog.objects.filter(email_type='campaign').exists())

    def test_prefill_falls_back_without_workshop(self):
        legacy = Event.objects.create(
            title='Legacy Talk', slug='legacy-talk',
            start_datetime=datetime(2026, 3, 1, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 3, 1, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=legacy',
        )
        response = self.client.get(self._prefill_url(legacy))
        self.assertEqual(response.status_code, 200)
        # Generic fallback line, the event title and recording link present.
        self.assertContains(response, 'Legacy Talk')
        self.assertContains(response, 'recording is now')
        # The recording link is the on-site event page (where members watch).
        self.assertContains(response, legacy.get_absolute_url())

    def test_non_staff_cannot_reach_prefill(self):
        self.client.logout()
        User.objects.create_user(
            email='member@test.com', password='pw', email_verified=True,
        )
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(self._prefill_url())
        self.assertIn(response.status_code, (302, 403))
        self.assertNotEqual(response.status_code, 200)

    def test_subject_template_db_override_applies(self):
        IntegrationSetting.objects.create(
            key='RECORDING_AVAILABLE_SUBJECT_TEMPLATE',
            value='Watch {event_title} now',
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        response = self.client.get(self._prefill_url())
        self.assertContains(response, 'Watch Shipping Agents Workshop now')

    def test_audience_picker_comment_does_not_leak_on_create(self):
        """The template comment must not render as visible text (#1076).

        A multi-line Django ``{# #}`` comment leaks because that syntax is
        single-line only; this guards the ``{% comment %}`` conversion on
        both the plain and pre-filled create forms.
        """
        for url in (reverse('studio_campaign_create'), self._prefill_url()):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(
                    response, 'optional event-registrant audience',
                )
                self.assertNotContains(
                    response, 'optional additional narrowing filter',
                )

    def test_audience_picker_comment_does_not_leak_on_edit(self):
        campaign = EmailCampaign.objects.create(
            subject='Existing', body='hi', status='draft',
        )
        response = self.client.get(
            reverse('studio_campaign_edit', kwargs={'campaign_id': campaign.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'optional event-registrant audience')
        self.assertNotContains(response, 'optional additional narrowing filter')
