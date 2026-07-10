from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Article
from email_app.models import EmailLog
from email_app.models.ses_event import SesEvent
from events.models import Event, EventSeries
from payments.models import PaymentAccountMismatch
from plans.models import Sprint
from questionnaires.models import Persona, Questionnaire

User = get_user_model()


class StudioListBaseline1193Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='studio-list-baseline@test.com',
            password='testpass123',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(
            email='studio-list-baseline@test.com',
            password='testpass123',
        )

    def test_articles_paginate_and_preserve_search_query(self):
        today = timezone.localdate()
        for index in range(30):
            Article.objects.create(
                title=f'Agent Article {index:02d}',
                slug=f'agent-article-{index:02d}',
                date=today,
            )
        Article.objects.create(title='Other Article', slug='other', date=today)

        response = self.client.get('/studio/articles/?q=Agent')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agent Article 00')
        self.assertNotContains(response, 'Other Article')
        self.assertContains(response, 'data-testid="article-list-pager"')
        self.assertContains(response, 'q=Agent&amp;page=2')

        clamped = self.client.get('/studio/articles/?q=Agent&page=999')
        self.assertEqual(clamped.context['page'].number, 2)
        self.assertContains(clamped, 'Agent Article 29')

    def test_events_use_operator_datetime_and_paginate_upcoming(self):
        start = timezone.now() + timedelta(days=2)
        for index in range(26):
            Event.objects.create(
                title=f'Operator Event {index:02d}',
                slug=f'operator-event-{index:02d}',
                start_datetime=start + timedelta(days=index),
                status='upcoming',
            )

        response = self.client.get('/studio/events/?q=Operator')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-upcoming-list-pager"')
        self.assertContains(response, 'q=Operator&amp;page=2')
        self.assertIn('data-testid="event-row-date"', html)
        self.assertIn('whitespace-nowrap', html)
        self.assertRegex(html, r'20\d\d-\d\d-\d\d \d\d:\d\d')
        self.assertNotIn('Fri, ', html)

    def test_sprints_search_filter_empty_state_and_primary_action(self):
        Sprint.objects.create(
            name='Agent Sprint',
            slug='agent-sprint',
            start_date=date(2026, 1, 1),
            duration_weeks=6,
            status='active',
        )
        Sprint.objects.create(
            name='Unrelated Sprint',
            slug='unrelated-sprint',
            start_date=date(2026, 2, 1),
            duration_weeks=6,
            status='draft',
        )

        response = self.client.get('/studio/sprints/?q=Agent')

        self.assertContains(response, 'data-component="studio-list-filter"')
        self.assertContains(response, 'Agent Sprint')
        self.assertNotContains(response, 'Unrelated Sprint')
        self.assertContains(response, '>Edit</a>')
        self.assertContains(response, '>View</a>')

        empty = self.client.get('/studio/sprints/?q=nomatch')
        self.assertContains(empty, 'data-testid="studio-empty-state-filter"')
        self.assertContains(empty, 'Clear filters')

    def test_event_series_search_and_cadence_cleanup(self):
        EventSeries.objects.create(
            name='Agent Office Hours',
            slug='agent-office-hours',
            cadence='weekly',
            day_of_week=2,
            start_time=time(16, 0),
            timezone='UTC',
        )
        EventSeries.objects.create(
            name='Other Series',
            slug='other-series',
            cadence='weekly',
            day_of_week=2,
            start_time=time(17, 0),
            timezone='UTC',
        )

        response = self.client.get('/studio/event-series/?q=Agent')

        self.assertContains(response, 'data-component="studio-list-filter"')
        self.assertContains(response, 'Agent Office Hours')
        self.assertNotContains(response, 'Other Series')
        self.assertContains(response, 'No occurrences scheduled')
        self.assertContains(response, '>Manage</a>')

    def test_questionnaires_and_personas_share_search_empty_and_pager_patterns(self):
        onboarding = Questionnaire.objects.create(
            title='Onboarding Agent Intake',
            slug='onboarding-agent-intake',
            purpose='onboarding',
        )
        Questionnaire.objects.create(
            title='Unrelated Questionnaire',
            slug='unrelated-questionnaire',
            purpose='general',
        )
        for index in range(26):
            Persona.objects.create(
                name=f'Priya Persona {index:02d}',
                archetype='Builder',
                slug=f'priya-persona-{index:02d}',
                default_questionnaire=onboarding,
                order=index,
            )
        Persona.objects.create(
            name='Sam Persona',
            archetype='Manager',
            slug='sam-persona',
            order=99,
        )

        questionnaires = self.client.get('/studio/questionnaires/?q=onboarding-agent')
        self.assertContains(questionnaires, 'data-component="studio-list-filter"')
        self.assertContains(questionnaires, 'Onboarding Agent Intake')
        self.assertNotContains(questionnaires, 'Unrelated Questionnaire')
        self.assertContains(questionnaires, '>Edit</a>')

        personas = self.client.get('/studio/personas/?q=Priya')
        self.assertContains(personas, 'data-testid="persona-list-pager"')
        self.assertContains(personas, 'q=Priya&amp;page=2')
        self.assertContains(personas, 'Priya Persona 00')
        self.assertNotContains(personas, 'Sam Persona')

        empty = self.client.get('/studio/personas/?q=missing-persona')
        self.assertContains(empty, 'data-testid="studio-empty-state-filter"')
        self.assertContains(empty, 'Clear filters')

    def test_payment_mismatches_search_paginate_and_use_shared_empty_state(self):
        paid = User.objects.create_user(
            email='paid-mismatch@test.com',
            password='testpass123',
        )
        candidate = User.objects.create_user(
            email='buyer-mismatch@test.com',
            password='testpass123',
        )
        for index in range(26):
            PaymentAccountMismatch.objects.create(
                stripe_session_id=f'cs_agent_{index:02d}',
                stripe_customer_id=f'cus_agent_{index:02d}',
                stripe_subscription_id=f'sub_agent_{index:02d}',
                stripe_email=f'buyer{index:02d}@example.com',
                paid_user=paid,
                candidate_user=candidate,
                reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
            )

        response = self.client.get(
            '/studio/users/payment-mismatches/?status=open&q=buyer',
        )

        self.assertContains(response, 'data-component="studio-list-filter"')
        self.assertContains(response, 'data-testid="payment-mismatch-list-pager"')
        self.assertContains(response, 'status=open&amp;q=buyer&amp;page=2')
        self.assertContains(response, 'buyer-mismatch@test.com')

        empty = self.client.get('/studio/users/payment-mismatches/?q=nomatch')
        self.assertContains(empty, 'data-testid="studio-empty-state-filter"')
        self.assertContains(empty, 'Clear filters')

    def test_ses_events_show_useful_diagnostic_columns_and_nowrap_dates(self):
        email_log = EmailLog.objects.create(
            user=self.staff,
            email_type='campaign',
            ses_message_id='ses-message-1193',
        )
        SesEvent.objects.create(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            message_id='sns-message-1193',
            raw_payload={'Message': 'payload'},
            recipient_email='bounce-target@example.com',
            user=self.staff,
            email_log=email_log,
            bounce_type='Permanent',
            bounce_subtype='NoEmail',
            action_taken='unsubscribed and tagged bounced',
        )

        response = self.client.get('/studio/ses-events/?type=bounce_permanent')
        html = response.content.decode()

        self.assertContains(response, 'bounce-target@example.com')
        self.assertContains(response, 'Permanent')
        self.assertContains(response, 'NoEmail')
        self.assertContains(response, 'campaign')
        self.assertIn('data-label="Received"', html)
        self.assertIn('whitespace-nowrap', html)
