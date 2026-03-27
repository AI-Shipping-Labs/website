"""Tests for voting admin configuration."""

from django.test import Client, TestCase

from accounts.models import User
from tests.fixtures import TierSetupMixin
from voting.models import Poll


class PollAdminTest(TierSetupMixin, TestCase):
    """Test Poll admin functional operations."""

    def test_admin_can_access_poll_list(self):
        """Admin user can access the poll changelist."""
        User.objects.create_superuser(
            email='admin@test.com', password='adminpass',
        )
        client = Client()
        client.login(email='admin@test.com', password='adminpass')
        response = client.get('/admin/voting/poll/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_access_poll_add(self):
        User.objects.create_superuser(
            email='admin2@test.com', password='adminpass',
        )
        client = Client()
        client.login(email='admin2@test.com', password='adminpass')
        response = client.get('/admin/voting/poll/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_poll(self):
        User.objects.create_superuser(
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
        User.objects.create_superuser(
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
