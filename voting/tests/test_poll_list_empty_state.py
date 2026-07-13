"""Poll-list shared empty-state regressions for issue #1227."""

from html.parser import HTMLParser

from django.test import TestCase

from accounts.models import User
from tests.fixtures import TierSetupMixin
from voting.models import Poll


class _EmptyStateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.text = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.depth:
            self.depth += 1
        elif tag == 'div' and attributes.get('data-testid') == 'member-empty-state':
            self.depth = 1
        if self.depth and tag == 'a':
            self.links.append(attributes.get('href'))

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data.strip())


def _parse_empty_state(response):
    parser = _EmptyStateParser()
    parser.feed(response.content.decode())
    return parser


class PollListEmptyStateTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gated_poll = Poll.objects.create(
            title='Hidden Main poll 1227',
            poll_type='topic',
            status='open',
        )
        cls.free_user = User.objects.create_user(
            email='free-empty-1227@example.com',
            password='testpass',
        )
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

    def test_anonymous_empty_state_has_exact_copy_and_sign_in_cta(self):
        response = self.client.get('/vote')
        empty_state = _parse_empty_state(response)
        empty_text = ' '.join(empty_state.text)

        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'data-empty-kind="fresh"')
        self.assertContains(response, 'data-lucide="vote"')
        self.assertIn('No active polls right now', empty_text)
        self.assertIn('Check back soon!', empty_text)
        self.assertIn('Sign in', empty_text)
        self.assertEqual(empty_state.links, ['/accounts/login/'])
        self.assertNotContains(response, 'Hidden Main poll 1227')

    def test_authenticated_free_member_has_no_sign_in_cta(self):
        self.client.force_login(self.free_user)

        response = self.client.get('/vote')
        empty_state = _parse_empty_state(response)
        empty_text = ' '.join(empty_state.text)

        self.assertContains(response, 'data-empty-kind="fresh"')
        self.assertIn('No active polls right now', empty_text)
        self.assertIn('Check back soon!', empty_text)
        self.assertNotIn('Sign in', empty_text)
        self.assertEqual(empty_state.links, [])
        self.assertNotContains(response, 'Hidden Main poll 1227')

    def test_accessible_poll_keeps_existing_non_empty_listing(self):
        main_user = User.objects.create_user(
            email='main-poll-1227@example.com',
            password='testpass',
        )
        main_user.tier = self.main_tier
        main_user.save()
        self.client.force_login(main_user)

        response = self.client.get('/vote')

        self.assertContains(response, 'Hidden Main poll 1227')
        self.assertNotContains(response, 'No active polls right now')
