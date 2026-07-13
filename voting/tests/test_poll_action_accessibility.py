import re

from django.test import TestCase, tag

from accounts.models import User
from tests.fixtures import TierSetupMixin
from voting.models import Poll, PollOption, PollVote

REQUIRED_CLASSES = {
    'min-h-[44px]',
    'focus-visible:outline-none',
    'focus-visible:ring-2',
    'focus-visible:ring-accent',
    'focus-visible:ring-offset-2',
    'focus-visible:ring-offset-background',
}


def _opening_tags(html, token):
    return re.findall(
        rf'<(?:a|button)\b[^>]*{re.escape(token)}[^>]*>',
        html,
        flags=re.DOTALL,
    )


def _classes(opening_tag):
    return set(re.search(r'class="([^"]+)"', opening_tag).group(1).split())


@tag('visual_regression')
class PollActionAccessibilityTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.topic_poll = Poll.objects.create(
            title='Accessible topic poll',
            poll_type='topic',
            status='open',
            allow_proposals=True,
            max_votes_per_user=2,
        )
        cls.first_option = PollOption.objects.create(
            poll=cls.topic_poll, title='First option'
        )
        cls.second_option = PollOption.objects.create(
            poll=cls.topic_poll, title='Second option'
        )
        cls.course_poll = Poll.objects.create(
            title='Gated course poll',
            poll_type='course',
            status='open',
        )
        PollOption.objects.create(poll=cls.course_poll, title='Course option')
        cls.main_user = User.objects.create_user(
            email='poll-a11y-main@test.com', password='testpass'
        )
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

    def _assert_contract(self, opening_tag):
        self.assertTrue(REQUIRED_CLASSES.issubset(_classes(opening_tag)))

    def test_rendered_gated_pricing_action_has_accessibility_contract(self):
        self.client.force_login(self.main_user)
        response = self.client.get(f'/vote/{self.course_poll.id}')
        html = response.content.decode()
        actions = _opening_tags(html, 'data-testid="poll-pricing-cta"')
        self.assertEqual(len(actions), 1)
        self._assert_contract(actions[0])
        self.assertIn('href="/pricing"', actions[0])

    def test_rendered_vote_voted_and_proposal_actions_have_contract(self):
        PollVote.objects.create(
            poll=self.topic_poll,
            option=self.first_option,
            user=self.main_user,
        )
        self.client.force_login(self.main_user)
        response = self.client.get(f'/vote/{self.topic_poll.id}')
        html = response.content.decode()

        vote_actions = _opening_tags(html, 'class="vote-btn')
        self.assertEqual(len(vote_actions), 2)
        for action in vote_actions:
            self._assert_contract(action)
        self.assertContains(response, 'Voted')
        self.assertContains(response, 'Vote')

        proposal_actions = _opening_tags(
            html, 'data-testid="poll-proposal-submit"'
        )
        self.assertEqual(len(proposal_actions), 1)
        self._assert_contract(proposal_actions[0])
