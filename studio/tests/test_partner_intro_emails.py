"""Studio tests for sprint partner intro emails (#1124)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import EmailLog
from plans.models import (
    Plan,
    Sprint,
    SprintEnrollment,
    SprintPartnerIntroEmailLog,
)
from plans.services import assign_accountability_partners

User = get_user_model()


@tag('core')
class StudioPartnerIntroEmailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May Sprint',
            slug='may-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )

    def _detail_url(self):
        return f'/studio/sprints/{self.sprint.pk}/'

    def _send_url(self):
        return f'/studio/sprints/{self.sprint.pk}/send-partner-intro-emails/'

    def _login_staff(self):
        self.client.login(email='staff@test.com', password='pw')

    def _member(self, email, **fields):
        return User.objects.create_user(email=email, password='pw', **fields)

    def _enroll(self, user, *, with_plan=True):
        SprintEnrollment.objects.create(
            sprint=self.sprint,
            user=user,
            enrolled_by=self.staff,
        )
        if with_plan:
            Plan.objects.create(member=user, sprint=self.sprint)

    def _ready_pair(self, *, bob_kwargs=None):
        alice = self._member('alice@test.com')
        bob = self._member('bob@test.com', **(bob_kwargs or {}))
        self._enroll(alice)
        self._enroll(bob)
        assign_accountability_partners(
            sprint=self.sprint,
            member=alice,
            partner=bob,
            assigned_by=self.staff,
        )
        return alice, bob

    def test_panel_shows_ready_counts_and_enabled_button(self):
        self._ready_pair()
        self._login_staff()

        response = self.client.get(self._detail_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="partner-intro-email-panel"')
        self.assertContains(response, 'data-testid="partner-intro-total-count">2<')
        self.assertContains(response, 'data-testid="partner-intro-eligible-count">2<')
        self.assertContains(response, 'data-testid="partner-intro-already-count">0<')
        self.assertContains(response, 'Sprint is ready.')
        self.assertNotContains(response, 'data-testid="partner-intro-email-button" disabled')

    def test_panel_blocks_missing_plan_and_names_member(self):
        alice = self._member('alice@test.com')
        bob = self._member('bob@test.com')
        self._enroll(alice)
        self._enroll(bob, with_plan=False)
        assign_accountability_partners(
            sprint=self.sprint,
            member=alice,
            partner=bob,
            assigned_by=self.staff,
        )
        self._login_staff()

        response = self.client.get(self._detail_url())

        self.assertContains(response, 'data-testid="partner-intro-missing-plan-count">1<')
        self.assertContains(response, 'bob@test.com')
        self.assertContains(response, 'Create sprint plans for every enrolled member')
        self.assertContains(response, 'data-testid="partner-intro-email-button"', count=1)
        self.assertContains(response, 'disabled')

    def test_panel_warns_for_missing_slack_link_but_stays_enabled(self):
        self._ready_pair()
        self._login_staff()

        response = self.client.get(self._detail_url())

        self.assertContains(response, 'data-testid="partner-intro-slack-warning-list"')
        self.assertContains(response, 'Missing Slack profile links')
        self.assertNotContains(response, 'data-testid="partner-intro-email-button" disabled')

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_post_sends_eligible_and_redirects_with_summary(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        self._ready_pair()
        self._login_staff()

        response = self.client.post(self._send_url(), follow=True)

        self.assertRedirects(response, self._detail_url())
        messages = [str(message) for message in response.context['messages']]
        self.assertTrue(
            any('2 sent, 0 skipped, 0 failed' in message for message in messages),
        )
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 2)
        self.assertEqual(
            EmailLog.objects.filter(email_type='sprint_partner_intro').count(),
            2,
        )
        self.assertContains(response, 'data-testid="partner-intro-eligible-count">0<')
        self.assertContains(response, 'data-testid="partner-intro-already-count">2<')

    def test_anonymous_and_non_staff_have_no_side_effects(self):
        self._ready_pair()

        anon_response = self.client.post(self._send_url())
        self.client.login(email='member@test.com', password='pw')
        non_staff_response = self.client.post(self._send_url())

        self.assertEqual(anon_response.status_code, 302)
        self.assertIn('/accounts/login/', anon_response['Location'])
        self.assertEqual(non_staff_response.status_code, 403)
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)
