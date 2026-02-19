"""Tests for voting views (poll list and poll detail pages)."""

from datetime import timedelta

from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from content.access import LEVEL_MAIN, LEVEL_PREMIUM
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


class PollListViewTest(TierSetupMixin, TestCase):
    """Test GET /vote - poll listing page."""

    def setUp(self):
        self.client = Client()
        self.topic_poll = Poll.objects.create(
            title='Topic Poll', description='Vote on topics',
            poll_type='topic', status='open',
        )
        self.course_poll = Poll.objects.create(
            title='Course Poll', description='Vote on courses',
            poll_type='course', status='open',
        )
        self.closed_poll = Poll.objects.create(
            title='Closed Poll', poll_type='topic', status='closed',
        )

    def test_anonymous_sees_no_polls(self):
        """Anonymous users (level 0) cannot access topic (20) or course (30) polls."""
        response = self.client.get('/vote')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Topic Poll')
        self.assertNotContains(response, 'Course Poll')

    def test_main_user_sees_topic_poll(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/vote')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Topic Poll')
        self.assertNotContains(response, 'Course Poll')  # requires premium

    def test_premium_user_sees_both_polls(self):
        user = User.objects.create_user(email='premium@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='premium@test.com', password='testpass')
        response = self.client.get('/vote')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Topic Poll')
        self.assertContains(response, 'Course Poll')

    def test_closed_poll_not_shown(self):
        user = User.objects.create_user(email='prem@test.com', password='testpass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem@test.com', password='testpass')
        response = self.client.get('/vote')
        self.assertNotContains(response, 'Closed Poll')

    def test_expired_poll_not_shown(self):
        """Polls past their closes_at are not shown in list."""
        expired = Poll.objects.create(
            title='Expired Poll',
            poll_type='topic',
            closes_at=timezone.now() - timedelta(hours=1),
        )
        user = User.objects.create_user(email='main2@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main2@test.com', password='testpass')
        response = self.client.get('/vote')
        self.assertNotContains(response, 'Expired Poll')

    def test_poll_shows_option_count(self):
        PollOption.objects.create(poll=self.topic_poll, title='Opt A')
        PollOption.objects.create(poll=self.topic_poll, title='Opt B')
        user = User.objects.create_user(email='main3@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main3@test.com', password='testpass')
        response = self.client.get('/vote')
        self.assertContains(response, '2 options')

    def test_basic_user_cannot_see_polls(self):
        """Basic users (level 10) cannot see topic polls (level 20)."""
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/vote')
        self.assertNotContains(response, 'Topic Poll')


class PollDetailViewTest(TierSetupMixin, TestCase):
    """Test GET /vote/{id} - poll detail page."""

    def setUp(self):
        self.client = Client()
        self.poll = Poll.objects.create(
            title='Test Poll', description='A test poll',
            poll_type='topic', status='open',
            allow_proposals=True, max_votes_per_user=2,
        )
        self.opt_a = PollOption.objects.create(poll=self.poll, title='Option A')
        self.opt_b = PollOption.objects.create(poll=self.poll, title='Option B')
        self.opt_c = PollOption.objects.create(poll=self.poll, title='Option C')

        # Create some votes: B has 3, A has 1, C has 0
        for i in range(3):
            u = User.objects.create_user(email=f'voter{i}@test.com')
            u.tier = self.main_tier
            u.save()
            PollVote.objects.create(poll=self.poll, option=self.opt_b, user=u)
        voter_a = User.objects.create_user(email='voterA@test.com')
        voter_a.tier = self.main_tier
        voter_a.save()
        PollVote.objects.create(poll=self.poll, option=self.opt_a, user=voter_a)

    def test_gated_for_anonymous(self):
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upgrade to Main to participate in this poll')

    def test_gated_for_basic_user(self):
        user = User.objects.create_user(email='basic@test.com', password='testpass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertContains(response, 'Upgrade to Main')

    def test_accessible_for_main_user(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Poll')
        self.assertContains(response, 'Option A')
        self.assertContains(response, 'Option B')
        self.assertContains(response, 'Option C')

    def test_options_sorted_by_vote_count(self):
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        content = response.content.decode()
        # Option B (3 votes) should appear before Option A (1 vote)
        pos_b = content.index('Option B')
        pos_a = content.index('Option A')
        pos_c = content.index('Option C')
        self.assertLess(pos_b, pos_a)
        self.assertLess(pos_a, pos_c)

    def test_user_voted_shown(self):
        user = User.objects.create_user(email='main2@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        PollVote.objects.create(poll=self.poll, option=self.opt_a, user=user)
        self.client.login(email='main2@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertContains(response, 'Voted')

    def test_proposal_form_shown_when_allowed(self):
        user = User.objects.create_user(email='main3@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main3@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertContains(response, 'Propose a New Option')

    def test_proposal_form_hidden_when_not_allowed(self):
        self.poll.allow_proposals = False
        self.poll.save()
        user = User.objects.create_user(email='main4@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main4@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertNotContains(response, 'Propose a New Option')

    def test_closed_poll_shows_results_read_only(self):
        self.poll.status = 'closed'
        self.poll.save()
        user = User.objects.create_user(email='main5@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main5@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Closed')
        self.assertContains(response, 'Option A')
        # Should not show vote buttons
        self.assertNotContains(response, 'class="vote-btn')

    def test_404_for_invalid_poll_id(self):
        import uuid
        fake_id = uuid.uuid4()
        response = self.client.get(f'/vote/{fake_id}')
        self.assertEqual(response.status_code, 404)

    def test_votes_remaining_shown(self):
        user = User.objects.create_user(email='main6@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main6@test.com', password='testpass')
        response = self.client.get(f'/vote/{self.poll.id}')
        # max_votes_per_user is 2, user has 0 votes
        self.assertContains(response, '2')  # votes remaining

    def test_course_poll_gated_for_main_user(self):
        course_poll = Poll.objects.create(
            title='Course Poll', poll_type='course', status='open',
        )
        user = User.objects.create_user(email='main7@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main7@test.com', password='testpass')
        response = self.client.get(f'/vote/{course_poll.id}')
        self.assertContains(response, 'Upgrade to Premium')
