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

    def test_create_topic_poll(self):
        poll = Poll.objects.create(
            title='What topic next?',
            description='Vote on the next topic',
            poll_type='topic',
        )
        self.assertEqual(poll.title, 'What topic next?')
        self.assertEqual(poll.poll_type, 'topic')
        self.assertEqual(poll.required_level, LEVEL_MAIN)
        self.assertEqual(poll.status, 'open')
        self.assertFalse(poll.allow_proposals)
        self.assertEqual(poll.max_votes_per_user, 3)
        self.assertIsNone(poll.closes_at)
        self.assertIsNotNone(poll.created_at)
        self.assertIsNotNone(poll.id)

    def test_create_course_poll(self):
        poll = Poll.objects.create(
            title='What course next?',
            poll_type='course',
        )
        self.assertEqual(poll.required_level, LEVEL_PREMIUM)

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

    def test_is_closed_by_status(self):
        poll = Poll.objects.create(title='Closed', status='closed')
        self.assertTrue(poll.is_closed)

    def test_is_closed_by_closes_at(self):
        poll = Poll.objects.create(
            title='Past',
            closes_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(poll.is_closed)

    def test_is_not_closed_when_open(self):
        poll = Poll.objects.create(title='Open', status='open')
        self.assertFalse(poll.is_closed)

    def test_is_not_closed_future_closes_at(self):
        poll = Poll.objects.create(
            title='Future',
            closes_at=timezone.now() + timedelta(days=7),
        )
        self.assertFalse(poll.is_closed)

    def test_total_votes_empty(self):
        poll = Poll.objects.create(title='Empty')
        self.assertEqual(poll.total_votes, 0)

    def test_total_votes_with_votes(self):
        poll = Poll.objects.create(title='Has votes')
        option = PollOption.objects.create(poll=poll, title='Option 1')
        user = User.objects.create_user(email='voter@test.com')
        PollVote.objects.create(poll=poll, option=option, user=user)
        self.assertEqual(poll.total_votes, 1)

    def test_options_count(self):
        poll = Poll.objects.create(title='Poll')
        PollOption.objects.create(poll=poll, title='A')
        PollOption.objects.create(poll=poll, title='B')
        self.assertEqual(poll.options_count, 2)

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

    def test_vote_count_empty(self):
        option = PollOption.objects.create(poll=self.poll, title='Test')
        self.assertEqual(option.vote_count, 0)

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

