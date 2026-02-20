"""Tests for voting admin configuration."""

from django.test import TestCase, Client
from django.contrib.admin.sites import AdminSite

from accounts.models import User
from payments.models import Tier
from voting.models import Poll, PollOption, PollVote
from voting.admin.poll import PollAdmin, PollOptionAdmin, PollVoteAdmin


class TierSetupMixin:
    @classmethod
    def setUpTestData(cls):
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )


class PollAdminTest(TierSetupMixin, TestCase):
    """Test Poll admin registration and configuration."""

    def setUp(self):
        self.site = AdminSite()
        self.admin = PollAdmin(Poll, self.site)

    def test_list_display_fields(self):
        self.assertIn('title', self.admin.list_display)
        self.assertIn('poll_type', self.admin.list_display)
        self.assertIn('status', self.admin.list_display)
        self.assertIn('required_level', self.admin.list_display)
        self.assertIn('allow_proposals', self.admin.list_display)

    def test_list_filter_fields(self):
        self.assertIn('status', self.admin.list_filter)
        self.assertIn('poll_type', self.admin.list_filter)

    def test_required_level_is_readonly(self):
        self.assertIn('required_level', self.admin.readonly_fields)

    def test_has_inline_options(self):
        self.assertEqual(len(self.admin.inlines), 1)
        self.assertEqual(self.admin.inlines[0].model, PollOption)

    def test_admin_can_access_poll_list(self):
        """Admin user can access the poll changelist."""
        admin_user = User.objects.create_superuser(
            email='admin@test.com', password='adminpass',
        )
        client = Client()
        client.login(email='admin@test.com', password='adminpass')
        response = client.get('/admin/voting/poll/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_access_poll_add(self):
        admin_user = User.objects.create_superuser(
            email='admin2@test.com', password='adminpass',
        )
        client = Client()
        client.login(email='admin2@test.com', password='adminpass')
        response = client.get('/admin/voting/poll/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_poll(self):
        admin_user = User.objects.create_superuser(
            email='admin3@test.com', password='adminpass',
        )
        client = Client()
        client.login(email='admin3@test.com', password='adminpass')
        response = client.post('/admin/voting/poll/add/', {
            'title': 'Admin Poll',
            'description': 'Created by admin',
            'poll_type': 'topic',
            'status': 'open',
            'allow_proposals': True,
            'max_votes_per_user': 3,
            # Inline formset management data
            'options-TOTAL_FORMS': '0',
            'options-INITIAL_FORMS': '0',
            'options-MIN_NUM_FORMS': '0',
            'options-MAX_NUM_FORMS': '1000',
        })
        # 302 = redirect after successful creation
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Poll.objects.filter(title='Admin Poll').exists())

    def test_admin_can_delete_poll(self):
        admin_user = User.objects.create_superuser(
            email='admin4@test.com', password='adminpass',
        )
        poll = Poll.objects.create(title='To Delete', poll_type='topic')
        client = Client()
        client.login(email='admin4@test.com', password='adminpass')
        response = client.post(
            f'/admin/voting/poll/{poll.id}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Poll.objects.filter(id=poll.id).exists())


class PollOptionAdminTest(TierSetupMixin, TestCase):
    """Test PollOption admin."""

    def test_list_display_fields(self):
        site = AdminSite()
        admin = PollOptionAdmin(PollOption, site)
        self.assertIn('title', admin.list_display)
        self.assertIn('poll', admin.list_display)
        self.assertIn('proposed_by', admin.list_display)


class PollVoteAdminTest(TierSetupMixin, TestCase):
    """Test PollVote admin."""

    def test_list_display_fields(self):
        site = AdminSite()
        admin = PollVoteAdmin(PollVote, site)
        self.assertIn('user', admin.list_display)
        self.assertIn('poll', admin.list_display)
        self.assertIn('option', admin.list_display)
