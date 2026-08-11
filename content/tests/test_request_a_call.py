"""Member-facing Request a call tests (#870, #1404)."""

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
            email='alice-call-profile@test.com', password='pw', tier=cls.free_tier,
        )
        _complete_onboarding(cls.onboarded)
        cls.not_onboarded = User.objects.create_user(
            email='bob-call-profile@test.com', password='pw', tier=cls.free_tier,
        )
        CallHost.objects.update(is_active=False, booking_url='')

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/request-a-call')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_not_onboarded_member_sees_gate_and_no_booking_links(self):
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, booking_url='https://example.com/valeria',
        )
        self.client.force_login(self.not_onboarded)
        response = self.client.get('/request-a-call')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="request-call-onboarding-gate"')
        self.assertNotContains(response, 'data-testid="call-host-book"')
        self.assertNotContains(response, 'https://example.com/valeria')

    def test_active_linked_profiles_render_in_order_regardless_of_legacy_load(self):
        CallHost.objects.filter(slug='alexey').update(
            name='Zed',
            is_active=True,
            booking_url='https://example.com/zed',
            capacity=0,
            current_load=99,
            order=0,
        )
        CallHost.objects.filter(slug='valeria').update(
            name='Amy',
            role_label='Co-founder',
            is_active=True,
            booking_url='https://example.com/amy',
            capacity=1,
            current_load=1,
            order=0,
        )
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertEqual([host.name for host in response.context['hosts']], ['Amy', 'Zed'])
        self.assertContains(response, 'https://example.com/zed')
        self.assertContains(response, 'https://example.com/amy')
        self.assertContains(response, 'Co-founder')
        self.assertEqual(response.content.decode().count('data-testid="call-host-book"'), 2)
        self.assertContains(response, 'target="_blank"', count=2)
        self.assertContains(response, 'rel="noopener noreferrer"', count=2)

    def test_hidden_and_linkless_profiles_are_omitted(self):
        CallHost.objects.filter(slug='alexey').update(
            is_active=False, booking_url='https://example.com/alexey',
        )
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, booking_url='',
        )
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertEqual(list(response.context['hosts']), [])
        self.assertNotContains(response, 'data-testid="call-host-card"')
        self.assertNotContains(response, 'data-testid="call-host-book"')
        self.assertNotContains(response, 'https://example.com/alexey')

    def test_active_profiles_with_unusable_legacy_urls_are_omitted(self):
        invalid_urls = (
            '   ',
            ' javascript:alert(1)',
            'ftp://example.com/book',
            'https://example.com/has space',
        )
        for index, booking_url in enumerate(invalid_urls):
            CallHost.objects.create(
                name=f'Invalid legacy profile {index}',
                slug=f'invalid-legacy-{index}',
                is_active=True,
                booking_url=booking_url,
            )
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertEqual(list(response.context['hosts']), [])
        self.assertNotContains(response, 'data-testid="call-host-card"')
        for index in range(len(invalid_urls)):
            self.assertNotContains(response, f'Invalid legacy profile {index}')

    def test_no_linked_profiles_uses_canonical_member_empty_state(self):
        self.client.force_login(self.onboarded)
        response = self.client.get('/request-a-call')
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'No call profiles available')
        self.assertContains(
            response,
            'There are no call booking links available right now. Check back soon.',
        )
        self.assertNotContains(response, '<div class="mt-10 grid')
        self.assertNotContains(response, 'No hosts are taking calls')
        self.assertNotContains(response, 'Not currently available for a call')

    def test_page_has_no_capacity_or_availability_language(self):
        CallHost.objects.filter(slug='valeria').update(
            is_active=True,
            booking_url='https://example.com/valeria',
            capacity=0,
            current_load=99,
        )
        self.client.force_login(self.onboarded)
        body = self.client.get('/request-a-call').content.decode()
        for forbidden in ('Capacity', 'Current load', 'Open spots', 'Paused', 'Full', 'Availability'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)


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
        return [action['url'] for action in response.context['quick_actions']]

    def test_onboarded_member_sees_request_call_quick_action(self):
        self.client.force_login(self.onboarded)
        response = self.client.get('/')
        self.assertIn('/request-a-call', self._quick_action_urls(response))

    def test_not_onboarded_member_has_no_request_call_action(self):
        self.client.force_login(self.not_onboarded)
        response = self.client.get('/')
        self.assertNotIn('/request-a-call', self._quick_action_urls(response))
