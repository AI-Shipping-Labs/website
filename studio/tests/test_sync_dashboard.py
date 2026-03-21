"""Tests for Studio content sync dashboard views."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from integrations.models import ContentSource, SyncLog

User = get_user_model()


class StudioSyncDashboardTest(TestCase):
    """Test the sync dashboard list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_returns_200(self):
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        response = self.client.get('/studio/sync/')
        self.assertTemplateUsed(response, 'studio/sync/dashboard.html')

    def test_dashboard_shows_sources(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'article')

    def test_dashboard_shows_content_path(self):
        """Content path must be prominently displayed so monorepo sources are distinguishable."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'blog/')
        self.assertContains(response, 'projects/')

    def test_dashboard_shows_sync_status(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'success')

    def test_dashboard_shows_never_synced(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Never synced')

    def test_dashboard_empty_state(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'No content sources configured')

    def test_dashboard_has_sync_all_button(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Sync All')

    def test_dashboard_has_sync_now_buttons(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Sync Now')
        self.assertContains(response, f'/studio/sync/{source.pk}/trigger/')

    def test_dashboard_has_history_links(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, f'/studio/sync/{source.pk}/')
        self.assertContains(response, 'History')

    def test_dashboard_requires_staff(self):
        client = Client()
        response = client.get('/studio/sync/')
        self.assertEqual(response.status_code, 302)

    def test_dashboard_non_staff_gets_403(self):
        client = Client()
        user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )
        client.login(email='user@test.com', password='testpass')
        response = client.get('/studio/sync/')
        self.assertEqual(response.status_code, 403)


class StudioSyncHistoryTest(TestCase):
    """Test the sync history view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )

    def test_history_returns_200(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertEqual(response.status_code, 200)

    def test_history_uses_correct_template(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertTemplateUsed(response, 'studio/sync/history.html')

    def test_history_shows_source_info(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'blog/')
        self.assertContains(response, 'article')

    def test_history_shows_sync_logs(self):
        SyncLog.objects.create(
            source=self.source,
            status='success',
            items_created=5,
            items_updated=2,
            items_deleted=0,
            finished_at=timezone.now(),
        )
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertContains(response, 'success')
        self.assertContains(response, '+5 created')
        self.assertContains(response, '2 updated')

    def test_history_shows_errors(self):
        SyncLog.objects.create(
            source=self.source,
            status='partial',
            errors=[{'file': 'test.md', 'error': 'parse error'}],
        )
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertContains(response, 'test.md')
        self.assertContains(response, 'parse error')

    def test_history_empty_state(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertContains(response, 'No sync history yet')

    def test_history_nonexistent_source_returns_404(self):
        import uuid
        fake_id = uuid.uuid4()
        response = self.client.get(f'/studio/sync/{fake_id}/')
        self.assertEqual(response.status_code, 404)

    def test_history_requires_staff(self):
        client = Client()
        response = client.get(f'/studio/sync/{self.source.pk}/')
        self.assertEqual(response.status_code, 302)

    def test_history_has_sync_now_button(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertContains(response, 'Sync Now')

    def test_history_has_back_link(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/')
        self.assertContains(response, '/studio/sync/')
        self.assertContains(response, 'Back to Content Sync')


class StudioSyncTriggerTest(TestCase):
    """Test the sync trigger endpoint."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )

    @patch('django_q.tasks.async_task')
    def test_trigger_redirects_to_dashboard(self, mock_async):
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_trigger_calls_sync(self, mock_async):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        mock_async.assert_called_once()
        args = mock_async.call_args
        self.assertEqual(args[0][0], 'integrations.services.github.sync_content_source')

    def test_trigger_requires_post(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 405)

    def test_trigger_requires_staff(self):
        client = Client()
        response = client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_trigger_nonexistent_source_returns_404(self):
        import uuid
        fake_id = uuid.uuid4()
        response = self.client.post(f'/studio/sync/{fake_id}/trigger/')
        self.assertEqual(response.status_code, 404)

    @patch('django_q.tasks.async_task', side_effect=Exception('queue error'))
    def test_trigger_handles_sync_error(self, mock_async):
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)


class StudioSyncAllTest(TestCase):
    """Test the sync all endpoint."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_sync_all_redirects_to_dashboard(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_sync_all_triggers_all_sources(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post('/studio/sync/all/')
        self.assertEqual(mock_async.call_count, 2)

    def test_sync_all_requires_post(self):
        response = self.client.get('/studio/sync/all/')
        self.assertEqual(response.status_code, 405)

    def test_sync_all_requires_staff(self):
        client = Client()
        response = client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)

    @patch('django_q.tasks.async_task')
    def test_sync_all_with_no_sources(self, mock_async):
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        mock_async.assert_not_called()


class StudioSyncStatusTest(TestCase):
    """Test the JSON status polling endpoint."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )

    def test_status_returns_json(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = json.loads(response.content)
        self.assertEqual(data['id'], str(self.source.pk))
        self.assertEqual(data['last_sync_status'], 'success')
        self.assertIsNotNone(data['last_synced_at'])

    def test_status_returns_null_for_never_synced(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
        )
        response = self.client.get(f'/studio/sync/{source.pk}/status/')
        data = json.loads(response.content)
        self.assertIsNone(data['last_sync_status'])
        self.assertIsNone(data['last_synced_at'])

    def test_status_requires_staff(self):
        client = Client()
        response = client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 302)

    def test_status_nonexistent_returns_404(self):
        import uuid
        fake_id = uuid.uuid4()
        response = self.client.get(f'/studio/sync/{fake_id}/status/')
        self.assertEqual(response.status_code, 404)


class StudioSidebarSyncLinkTest(TestCase):
    """Test that the Content Sync link appears in the Studio sidebar."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_sidebar_has_content_sync_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, '/studio/sync/')
        self.assertContains(response, 'Content Sync')
