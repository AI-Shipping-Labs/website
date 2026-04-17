"""Tests for the studio-wide worker-down banner and sync trigger warnings.

Covers:
- Banner appears at the top of every Studio page when no worker is running.
- Banner does NOT appear on public pages (banner is studio-only).
- Banner is suppressed when ``EXPECT_WORKER=false``.
- Sync trigger / sync-all surface a warning instead of a plain success when
  the worker isn't running.
"""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.models import ContentSource

User = get_user_model()


class StudioWorkerBannerTest(TestCase):
    """The red 'worker not running' banner on studio pages."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_banner_shown_on_studio_dashboard_when_no_worker(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop('EXPECT_WORKER', None)
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Background worker not running')
        self.assertContains(response, 'manage.py qcluster')

    def test_banner_hidden_on_studio_dashboard_when_worker_alive(self):
        # Build a fake cluster like other tests do
        from studio.tests.test_worker_health import _fake_cluster
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Background worker not running')

    def test_banner_hidden_when_expect_worker_false(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]), \
             patch.dict(os.environ, {'EXPECT_WORKER': 'false'}):
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Background worker not running')

    def test_banner_appears_on_other_studio_pages(self):
        """Banner is wired into studio/base.html — it must appear on any studio page."""
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Background worker not running')

    def test_banner_not_shown_on_public_pages(self):
        """Banner is studio-only; the home page must not include it."""
        self.client.logout()
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/')
        # 200 expected; page must not show the studio banner
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Background worker not running')

    def test_studio_worker_status_context_set_for_studio_paths(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/')
        self.assertIn('studio_worker_status', response.context)
        self.assertIsNotNone(response.context['studio_worker_status'])
        self.assertFalse(response.context['studio_worker_status']['alive'])

    def test_studio_worker_status_context_none_for_non_studio_paths(self):
        self.client.logout()
        response = self.client.get('/')
        # Context processor returns None for non-studio paths so the banner
        # template short-circuits without querying the broker.
        self.assertIsNone(response.context.get('studio_worker_status'))


class SyncTriggerWorkerWarningTest(TestCase):
    """Sync-now / sync-all should warn when no worker is available."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_sync_trigger_warns_when_worker_down(self, _mock_async):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.post(
                f'/studio/sync/{self.source.pk}/trigger/',
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        # The flash message should call out that the queue is filling but
        # nothing is processing.
        body = response.content.decode()
        self.assertIn('Sync queued', body)
        self.assertIn('worker is not running', body)
        self.assertIn('manage.py qcluster', body)

    @patch('django_q.tasks.async_task')
    def test_sync_trigger_plain_success_when_worker_alive(self, _mock_async):
        from studio.tests.test_worker_health import _fake_cluster
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            response = self.client.post(
                f'/studio/sync/{self.source.pk}/trigger/',
                follow=True,
            )
        body = response.content.decode()
        self.assertIn('Sync queued', body)
        self.assertNotIn('worker is not running', body)

    @patch('django_q.tasks.async_task')
    def test_sync_all_warns_when_worker_down(self, _mock_async):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.post('/studio/sync/all/', follow=True)
        body = response.content.decode()
        self.assertIn('Sync triggered', body)
        self.assertIn('worker is not running', body)


class SyncDashboardInlineWarningTest(TestCase):
    """The sync dashboard shows an inline yellow banner near the Sync All button."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_inline_warning_when_worker_down(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Worker not running')
        self.assertContains(response, 'manage.py qcluster')

    def test_no_inline_warning_when_worker_alive(self):
        from studio.tests.test_worker_health import _fake_cluster
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            response = self.client.get('/studio/sync/')
        # The phrase 'Worker not running' from the inline yellow box should
        # not be present when the cluster is alive.
        self.assertNotContains(response, 'Worker not running')
