"""Tests for voting views (poll list and poll detail pages)."""

from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import User
from tests.fixtures import TierSetupMixin
from voting.models import Poll, PollOption, PollVote


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
        Poll.objects.create(
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


class PollGatingTest(TierSetupMixin, TestCase):
    """Free user gating: no polls in list, gated detail with pricing CTA.

    Replaces playwright_tests/test_voting_polls.py::
    TestScenario5FreeMemberCannotAccessTopicPolls::
    test_free_member_sees_no_polls_and_gating_on_direct_access
    (template state only; no JS interaction).
    """

    def setUp(self):
        self.client = Client()
        self.poll = Poll.objects.create(
            title='Topic Poll for Main Members',
            poll_type='topic',
            status='open',
        )
        PollOption.objects.create(poll=self.poll, title='Option 1')
        self.free_user = User.objects.create_user(
            email='free@test.com', password='testpass',
        )
        self.free_user.tier = self.free_tier
        self.free_user.save()
        self.client.login(email='free@test.com', password='testpass')

    def test_free_user_list_shows_empty_state(self):
        """Free user (level 0) sees no topic polls (level 20) in list."""
        response = self.client.get('/vote')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Topic Poll for Main Members')
        self.assertContains(response, 'No active polls right now')
        self.assertEqual(list(response.context['polls']), [])

    def test_free_user_detail_shows_upgrade_to_main_cta(self):
        """Direct access to topic poll detail shows Upgrade to Main CTA."""
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_gated'])
        self.assertEqual(response.context['required_tier_name'], 'Main')
        self.assertContains(
            response,
            'Upgrade to Main to participate in this poll',
        )

    def test_free_user_detail_has_view_pricing_link(self):
        """Gated detail page renders a 'View Pricing' link to /pricing."""
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertContains(
            response,
            'href="/pricing"',
        )
        self.assertContains(response, 'View Pricing')


class CoursePollGatingTest(TierSetupMixin, TestCase):
    """Main user gating on Premium-only course polls.

    Replaces playwright_tests/test_voting_polls.py::
    TestScenario6MainMemberCannotAccessCoursePoll::
    test_main_member_sees_gating_on_course_poll
    (template state only; no JS interaction).
    """

    def setUp(self):
        self.client = Client()
        self.course_poll = Poll.objects.create(
            title='Next Mini-Course',
            poll_type='course',
            description='Vote for the next mini-course!',
            status='open',
        )
        PollOption.objects.create(poll=self.course_poll, title='Course Option A')
        self.main_user = User.objects.create_user(
            email='main@test.com', password='testpass',
        )
        self.main_user.tier = self.main_tier
        self.main_user.save()
        self.client.login(email='main@test.com', password='testpass')

    def test_course_poll_absent_from_list_for_main_user(self):
        """Course polls (level 30) are not listed for Main users (level 20)."""
        response = self.client.get('/vote')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Next Mini-Course')
        self.assertEqual(list(response.context['polls']), [])

    def test_course_poll_detail_shows_upgrade_to_premium_cta(self):
        """Direct access to a course poll shows Upgrade to Premium CTA."""
        response = self.client.get(f'/vote/{self.course_poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_gated'])
        self.assertEqual(response.context['required_tier_name'], 'Premium')
        self.assertContains(
            response,
            'Upgrade to Premium to participate in this poll',
        )

    def test_course_poll_detail_has_view_pricing_link(self):
        """Gated course poll detail renders the 'View Pricing' link."""
        response = self.client.get(f'/vote/{self.course_poll.id}')
        self.assertContains(response, 'href="/pricing"')
        self.assertContains(response, 'View Pricing')


class ClosedPollDisplayTest(TierSetupMixin, TestCase):
    """Closed polls render read-only with no vote buttons or proposal form.

    Replaces playwright_tests/test_voting_polls.py::
    TestScenario8ClosedPollReadOnly::
    test_closed_poll_shows_read_only_results
    (template state only; no JS interaction).
    """

    def setUp(self):
        self.client = Client()
        self.poll = Poll.objects.create(
            title='Closed Topic Poll',
            poll_type='topic',
            status='closed',
            allow_proposals=True,  # even with proposals enabled
        )
        self.opt_alpha = PollOption.objects.create(
            poll=self.poll, title='Option Alpha',
        )
        self.opt_beta = PollOption.objects.create(
            poll=self.poll, title='Option Beta',
        )
        self.opt_gamma = PollOption.objects.create(
            poll=self.poll, title='Option Gamma',
        )
        self.user = User.objects.create_user(
            email='main@test.com', password='testpass',
        )
        self.user.tier = self.main_tier
        self.user.save()
        other = User.objects.create_user(
            email='other@test.com', password='testpass',
        )
        other.tier = self.main_tier
        other.save()
        # Add some votes so vote counts are non-zero
        PollVote.objects.create(
            poll=self.poll, option=self.opt_alpha, user=self.user,
        )
        PollVote.objects.create(
            poll=self.poll, option=self.opt_alpha, user=other,
        )
        PollVote.objects.create(
            poll=self.poll, option=self.opt_beta, user=other,
        )
        self.client.login(email='main@test.com', password='testpass')

    def test_closed_poll_shows_closed_indicator(self):
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_closed'])
        self.assertFalse(response.context['can_vote'])
        # 'Closed' badge appears in the poll header
        self.assertContains(response, 'Closed')

    def test_closed_poll_lists_all_options(self):
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertContains(response, 'Option Alpha')
        self.assertContains(response, 'Option Beta')
        self.assertContains(response, 'Option Gamma')

    def test_closed_poll_renders_no_vote_buttons(self):
        response = self.client.get(f'/vote/{self.poll.id}')
        # The vote button is rendered with the vote-btn CSS class only
        # when can_vote is True; closed polls must not render any.
        self.assertNotContains(response, 'class="vote-btn')

    def test_closed_poll_hides_proposal_form_even_when_allowed(self):
        """allow_proposals=True is overridden by the closed status."""
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertFalse(response.context['allow_proposals'])
        self.assertNotContains(response, 'id="propose-form"')
        self.assertNotContains(response, 'Propose a New Option')


class AnonymousVotePromptTest(TierSetupMixin, TestCase):
    """Anonymous visitors are prompted to sign in on list and detail pages.

    Replaces playwright_tests/test_voting_polls.py::
    TestScenario9AnonymousVisitorPromptedToSignIn::
    test_anonymous_visitor_sees_sign_in_prompts
    (template state only; no JS interaction).
    """

    def setUp(self):
        self.client = Client()
        self.poll = Poll.objects.create(
            title='Public Topic Poll',
            poll_type='topic',
            status='open',
        )
        PollOption.objects.create(poll=self.poll, title='Option X')
        PollOption.objects.create(poll=self.poll, title='Option Y')

    def test_anonymous_list_hides_topic_poll_and_prompts_signin(self):
        """Anonymous (level 0) sees no topic polls and a sign-in link."""
        response = self.client.get('/vote')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Public Topic Poll')
        self.assertEqual(list(response.context['polls']), [])
        # Empty-state block contains the sign-in CTA copy specific to
        # the poll list (distinct from the global header Sign in button).
        self.assertContains(
            response,
            'to see polls available for your membership level',
        )
        self.assertContains(response, 'href="/accounts/login/"')

    def test_anonymous_detail_shows_gating_with_pricing_link(self):
        """Anonymous direct access to topic poll detail is gated."""
        response = self.client.get(f'/vote/{self.poll.id}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_gated'])
        # Anon users see the same Upgrade-to-Main path as Free users
        self.assertContains(
            response,
            'Upgrade to Main to participate in this poll',
        )
        self.assertContains(response, 'View Pricing')
