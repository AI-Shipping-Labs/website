"""Tests for the member-facing /request-a-call page (#870)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from community.models import CallHost
from questionnaires.models import Questionnaire, Response
from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _complete_onboarding(user):
    questionnaire = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
    Response.objects.create(
        questionnaire=questionnaire, respondent=user, status='submitted',
    )


@tag('core')
class RequestACallGateTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.onboarded = User.objects.create_user(
            email='alice@test.com', password='pw', tier=cls.free_tier,
        )
        _complete_onboarding(cls.onboarded)
        cls.not_onboarded = User.objects.create_user(
            email='bob@test.com', password='pw', tier=cls.free_tier,
        )
        # Reset seeded hosts to a known state for these tests.
        CallHost.objects.update(is_active=False, capacity=0, current_load=0)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/request-a-call')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_not_onboarded_member_sees_gate_and_no_booking_links(self):
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, capacity=5, booking_url='https://example.com/v',
        )
        self.client.force_login(self.not_onboarded)
        response = self.client.get('/request-a-call')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['onboarded'])
        self.assertContains(response, 'data-testid="request-call-onboarding-gate"')
        # Issue #982: a Free member (no active override) cannot enter the
        # paid-only onboarding flow, so the "Finish onboarding" CTA into
        # /onboarding/ is NOT handed to them even though the gate copy shows.
        self.assertNotContains(response, 'data-testid="request-call-onboarding-cta"')
        self.assertNotContains(response, 'href="/onboarding/"')
        # No host booking links rendered for un-onboarded members.
        self.assertNotContains(response, 'data-testid="call-host-book"')
        self.assertNotContains(response, 'https://example.com/v')

    def test_onboarded_member_sees_available_host_with_booking_link(self):
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, capacity=5, current_load=0,
            booking_url='https://example.com/valeria',
            role_label='Co-founder',
        )
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['onboarded'])
        self.assertContains(response, 'data-testid="call-host-book"')
        self.assertContains(response, 'https://example.com/valeria')
        self.assertContains(response, 'Co-founder')

    def test_unavailable_host_shows_status_no_link(self):
        # Alexey full (capacity reached), Valeria available.
        CallHost.objects.filter(slug='alexey').update(
            is_active=True, capacity=1, current_load=1,
            booking_url='https://example.com/alexey',
        )
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, capacity=5, current_load=0,
            booking_url='https://example.com/valeria',
        )
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertContains(response, 'data-testid="call-host-unavailable"')
        self.assertContains(response, 'Not currently available for a call')
        # Alexey's booking link must not be rendered while he is full.
        self.assertNotContains(response, 'https://example.com/alexey')
        # Valeria's link is still rendered.
        self.assertContains(response, 'https://example.com/valeria')

    def test_all_full_shows_check_back_line_not_blank(self):
        CallHost.objects.update(is_active=True, capacity=1, current_load=1)
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertContains(response, 'data-testid="request-call-check-back"')
        self.assertContains(response, 'check back')
        self.assertFalse(response.context['any_available'])
        # Both faces still rendered.
        self.assertEqual(response.content.decode().count('data-testid="call-host-card"'), 2)
        self.assertNotContains(response, 'data-testid="call-host-book"')

    def test_inactive_host_hidden_from_page(self):
        CallHost.objects.filter(slug='alexey').update(is_active=False)
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, capacity=5, booking_url='https://example.com/v',
        )
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        host_slugs = [h.slug for h in response.context['hosts']]
        self.assertNotIn('alexey', host_slugs)
        self.assertIn('valeria', host_slugs)


@tag('core')
class DashboardRequestCallEntryPointTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.onboarded = User.objects.create_user(
            email='done@test.com', password='pw', tier=cls.free_tier,
        )
        _complete_onboarding(cls.onboarded)
        cls.not_onboarded = User.objects.create_user(
            email='todo@test.com', password='pw', tier=cls.free_tier,
        )

    def _quick_action_urls(self, response):
        return [a['url'] for a in response.context['quick_actions']]

    def test_onboarded_member_sees_request_call_quick_action(self):
        self.client.force_login(self.onboarded)
        response = self.client.get('/')
        self.assertIn('/request-a-call', self._quick_action_urls(response))

    def test_not_onboarded_member_has_no_request_call_action(self):
        self.client.force_login(self.not_onboarded)
        response = self.client.get('/')
        self.assertNotIn('/request-a-call', self._quick_action_urls(response))
