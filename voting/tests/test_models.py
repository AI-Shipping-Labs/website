"""Tests for voting models (Poll, PollOption, PollVote)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from tests.fixtures import TierSetupMixin
from voting.models import Poll, PollOption, PollVote


class PollModelTest(TierSetupMixin, TestCase):
    """Test Poll model creation and behavior."""

    # ``test_create_topic_poll`` / ``test_create_course_poll``
    # previously round-tripped a ``Poll`` row to assert default
    # field values — Django ORM behaviour. The
    # ``required_level`` auto-mapping by ``poll_type`` is the
    # actual project rule worth testing, and that is covered by
    # ``test_required_level_auto_set_on_save`` /
    # ``test_required_level_changes_with_poll_type`` below.
    def test_required_level_auto_set_on_save(self):
        """required_level is auto-set based on poll_type, even if explicitly provided."""
        poll = Poll.objects.create(
            title='Test',
            poll_type='topic',
            required_level=99,  # Should be overridden
        )
        self.assertEqual(poll.required_level, LEVEL_MAIN)

    def test_required_level_changes_with_poll_type(self):
        poll = Poll.objects.create(title='Test', poll_type='topic')
        self.assertEqual(poll.required_level, LEVEL_MAIN)
        poll.poll_type = 'course'
        poll.save()
        poll.refresh_from_db()
        self.assertEqual(poll.required_level, LEVEL_PREMIUM)

    def test_is_closed_truth_table(self):
        # ``Poll.is_closed`` is True when ``status == 'closed'`` OR
        # ``closes_at`` is in the past. The four-row truth table:
        now = timezone.now()
        cases = [
            # (kwargs, expected_is_closed, why)
            ({'status': 'closed'}, True, 'status=closed forces closed'),
            (
                {'closes_at': now - timedelta(hours=1)},
                True,
                'closes_at in the past forces closed',
            ),
            ({'status': 'open'}, False, 'open status, no closes_at'),
            (
                {'closes_at': now + timedelta(days=7)},
                False,
                'closes_at in the future is still open',
            ),
        ]
        for kwargs, expected, why in cases:
            with self.subTest(why=why):
                poll = Poll.objects.create(title='is_closed', **kwargs)
                self.assertEqual(poll.is_closed, expected)

    def test_total_votes_with_votes(self):
        poll = Poll.objects.create(title='Has votes')
        option = PollOption.objects.create(poll=poll, title='Option 1')
        user = User.objects.create_user(email='voter@test.com')
        PollVote.objects.create(poll=poll, option=option, user=user)
        self.assertEqual(poll.total_votes, 1)

    def test_get_absolute_url(self):
        poll = Poll.objects.create(title='Test')
        self.assertEqual(poll.get_absolute_url(), f'/vote/{poll.id}')


class PollOptionModelTest(TierSetupMixin, TestCase):
    """Test PollOption model."""

    def setUp(self):
        self.poll = Poll.objects.create(title='Test Poll')

    def test_create_admin_option(self):
        option = PollOption.objects.create(
            poll=self.poll,
            title='RAG Pipelines',
            description='Learn about RAG',
        )
        self.assertEqual(option.title, 'RAG Pipelines')
        self.assertIsNone(option.proposed_by)
        self.assertIsNotNone(option.created_at)

    def test_create_user_proposed_option(self):
        user = User.objects.create_user(email='proposer@test.com')
        option = PollOption.objects.create(
            poll=self.poll,
            title='MCP Servers',
            proposed_by=user,
        )
        self.assertEqual(option.proposed_by, user)

    def test_vote_count_with_votes(self):
        option = PollOption.objects.create(poll=self.poll, title='Test')
        user = User.objects.create_user(email='v@test.com')
        PollVote.objects.create(poll=self.poll, option=option, user=user)
        self.assertEqual(option.vote_count, 1)


class PollVoteModelTest(TierSetupMixin, TestCase):
    """Test PollVote model and unique constraint."""

    def setUp(self):
        self.poll = Poll.objects.create(title='Test Poll')
        self.option = PollOption.objects.create(poll=self.poll, title='Option 1')
        self.user = User.objects.create_user(email='voter@test.com')

    def test_create_vote(self):
        vote = PollVote.objects.create(
            poll=self.poll, option=self.option, user=self.user,
        )
        self.assertEqual(vote.poll, self.poll)
        self.assertEqual(vote.option, self.option)
        self.assertEqual(vote.user, self.user)
        self.assertIsNotNone(vote.created_at)

    def test_same_user_different_options(self):
        """User can vote on multiple different options."""
        option2 = PollOption.objects.create(poll=self.poll, title='Option 2')
        PollVote.objects.create(poll=self.poll, option=self.option, user=self.user)
        PollVote.objects.create(poll=self.poll, option=option2, user=self.user)
        self.assertEqual(PollVote.objects.filter(user=self.user).count(), 2)

    def test_different_users_same_option(self):
        """Different users can vote on the same option."""
        user2 = User.objects.create_user(email='voter2@test.com')
        PollVote.objects.create(poll=self.poll, option=self.option, user=self.user)
        PollVote.objects.create(poll=self.poll, option=self.option, user=user2)
        self.assertEqual(self.option.vote_count, 2)

