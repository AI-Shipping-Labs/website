"""Tests for voting API endpoints (vote toggle and propose)."""

import json
import uuid

from django.test import TestCase, Client
from django.utils import timezone
from datetime import timedelta

from accounts.models import User
from payments.models import Tier
from voting.models import Poll, PollOption, PollVote


class TierSetupMixin:
    """Mixin that creates the standard tiers for testing."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug='main', defaults={'name': 'Main', 'level': 20},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug='premium', defaults={'name': 'Premium', 'level': 30},
        )


class VoteToggleAPITest(TierSetupMixin, TestCase):
    """Test POST /api/vote/{id}/vote endpoint."""

    def setUp(self):
        self.client = Client()
        self.poll = Poll.objects.create(
            title='Test Poll', poll_type='topic', status='open',
            max_votes_per_user=2,
        )
        self.option_a = PollOption.objects.create(poll=self.poll, title='A')
        self.option_b = PollOption.objects.create(poll=self.poll, title='B')
        self.option_c = PollOption.objects.create(poll=self.poll, title='C')
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()

    def _vote(self, poll_id, option_id, user=None):
        """Helper to POST a vote."""
        if user:
            self.client.login(email=user.email, password='testpass')
        return self.client.post(
            f'/api/vote/{poll_id}/vote',
            data=json.dumps({'option_id': str(option_id)}),
            content_type='application/json',
        )

    def test_vote_requires_authentication(self):
        response = self._vote(self.poll.id, self.option_a.id)
        self.assertEqual(response.status_code, 401)

    def test_vote_creates_vote(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self._vote(self.poll.id, self.option_a.id)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['action'], 'voted')
        self.assertEqual(data['vote_count'], 1)
        self.assertTrue(
            PollVote.objects.filter(
                poll=self.poll, option=self.option_a, user=self.user,
            ).exists()
        )

    def test_vote_toggle_removes_vote(self):
        self.client.login(email='main@test.com', password='testpass')
        PollVote.objects.create(
            poll=self.poll, option=self.option_a, user=self.user,
        )
        response = self._vote(self.poll.id, self.option_a.id)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['action'], 'unvoted')
        self.assertEqual(data['vote_count'], 0)
        self.assertFalse(
            PollVote.objects.filter(
                poll=self.poll, option=self.option_a, user=self.user,
            ).exists()
        )

    def test_max_votes_enforced(self):
        """Returns 400 when user tries to exceed max_votes_per_user."""
        self.client.login(email='main@test.com', password='testpass')
        PollVote.objects.create(
            poll=self.poll, option=self.option_a, user=self.user,
        )
        PollVote.objects.create(
            poll=self.poll, option=self.option_b, user=self.user,
        )
        # Third vote should be rejected (max is 2)
        response = self._vote(self.poll.id, self.option_c.id)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('Maximum', data['error'])

    def test_vote_on_closed_poll_returns_403(self):
        self.poll.status = 'closed'
        self.poll.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self._vote(self.poll.id, self.option_a.id)
        self.assertEqual(response.status_code, 403)

    def test_vote_on_expired_poll_returns_403(self):
        self.poll.closes_at = timezone.now() - timedelta(hours=1)
        self.poll.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self._vote(self.poll.id, self.option_a.id)
        self.assertEqual(response.status_code, 403)

    def test_vote_insufficient_access_level(self):
        """Basic user cannot vote on topic poll (requires Main)."""
        basic_user = User.objects.create_user(email='basic@test.com', password='testpass')
        basic_user.tier = self.basic_tier
        basic_user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self._vote(self.poll.id, self.option_a.id)
        self.assertEqual(response.status_code, 403)

    def test_vote_invalid_option_returns_404(self):
        self.client.login(email='main@test.com', password='testpass')
        fake_option_id = uuid.uuid4()
        response = self._vote(self.poll.id, fake_option_id)
        self.assertEqual(response.status_code, 404)

    def test_vote_option_from_different_poll_returns_404(self):
        other_poll = Poll.objects.create(title='Other', poll_type='topic')
        other_option = PollOption.objects.create(poll=other_poll, title='Other Opt')
        self.client.login(email='main@test.com', password='testpass')
        response = self._vote(self.poll.id, other_option.id)
        self.assertEqual(response.status_code, 404)

    def test_vote_invalid_json_returns_400(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.post(
            f'/api/vote/{self.poll.id}/vote',
            data='not json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_vote_missing_option_id_returns_400(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.post(
            f'/api/vote/{self.poll.id}/vote',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_vote_nonexistent_poll_returns_404(self):
        self.client.login(email='main@test.com', password='testpass')
        fake_poll_id = uuid.uuid4()
        response = self._vote(fake_poll_id, self.option_a.id)
        self.assertEqual(response.status_code, 404)

    def test_only_post_allowed(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/vote/{self.poll.id}/vote')
        self.assertEqual(response.status_code, 405)

    def test_unvote_frees_slot_for_new_vote(self):
        """After unvoting, user can vote on a different option within max."""
        self.client.login(email='main@test.com', password='testpass')
        PollVote.objects.create(
            poll=self.poll, option=self.option_a, user=self.user,
        )
        PollVote.objects.create(
            poll=self.poll, option=self.option_b, user=self.user,
        )
        # Unvote A
        self._vote(self.poll.id, self.option_a.id)
        # Now vote C should work
        response = self._vote(self.poll.id, self.option_c.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['action'], 'voted')


class ProposeOptionAPITest(TierSetupMixin, TestCase):
    """Test POST /api/vote/{id}/propose endpoint."""

    def setUp(self):
        self.client = Client()
        self.poll = Poll.objects.create(
            title='Proposals Poll', poll_type='topic', status='open',
            allow_proposals=True,
        )
        self.user = User.objects.create_user(email='main@test.com', password='testpass')
        self.user.tier = self.main_tier
        self.user.save()

    def _propose(self, poll_id, title, description=''):
        return self.client.post(
            f'/api/vote/{poll_id}/propose',
            data=json.dumps({'title': title, 'description': description}),
            content_type='application/json',
        )

    def test_propose_requires_authentication(self):
        response = self._propose(self.poll.id, 'New Topic')
        self.assertEqual(response.status_code, 401)

    def test_propose_creates_option(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self._propose(self.poll.id, 'RAG Pipelines', 'Learn RAG')
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['title'], 'RAG Pipelines')
        self.assertEqual(data['description'], 'Learn RAG')
        option = PollOption.objects.get(id=data['option_id'])
        self.assertEqual(option.proposed_by, self.user)
        self.assertEqual(option.poll, self.poll)

    def test_propose_not_allowed_returns_403(self):
        self.poll.allow_proposals = False
        self.poll.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self._propose(self.poll.id, 'New Topic')
        self.assertEqual(response.status_code, 403)

    def test_propose_on_closed_poll_returns_403(self):
        self.poll.status = 'closed'
        self.poll.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self._propose(self.poll.id, 'New Topic')
        self.assertEqual(response.status_code, 403)

    def test_propose_insufficient_access_returns_403(self):
        basic_user = User.objects.create_user(email='basic@test.com', password='testpass')
        basic_user.tier = self.basic_tier
        basic_user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self._propose(self.poll.id, 'New Topic')
        self.assertEqual(response.status_code, 403)

    def test_propose_empty_title_returns_400(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self._propose(self.poll.id, '')
        self.assertEqual(response.status_code, 400)

    def test_propose_whitespace_title_returns_400(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self._propose(self.poll.id, '   ')
        self.assertEqual(response.status_code, 400)

    def test_propose_invalid_json_returns_400(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.post(
            f'/api/vote/{self.poll.id}/propose',
            data='not json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_propose_nonexistent_poll_returns_404(self):
        self.client.login(email='main@test.com', password='testpass')
        fake_id = uuid.uuid4()
        response = self._propose(fake_id, 'New Topic')
        self.assertEqual(response.status_code, 404)

    def test_only_post_allowed(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/api/vote/{self.poll.id}/propose')
        self.assertEqual(response.status_code, 405)

    def test_propose_with_description_optional(self):
        self.client.login(email='main@test.com', password='testpass')
        response = self._propose(self.poll.id, 'Topic Only')
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['description'], '')
