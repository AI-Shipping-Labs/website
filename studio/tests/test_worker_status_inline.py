"""Tests for the subtle inline worker-status indicator and the
redirect-to-worker behaviour after a job is submitted.

Covers:
- The indicator renders on /studio/sync/, /studio/sync/history/,
  /studio/campaigns/, /studio/campaigns/<id>/, /studio/campaigns/new,
  and /studio/notifications/ in both worker-up and worker-down states.
- The studio dashboard at /studio/ shows the prominent worker status panel
  in both states.
- Sync trigger and Sync All redirect back to /studio/sync/ (the sync
  dashboard, NOT the worker page — see #239) with a flash message that
  includes a clickable link to /studio/worker/ via the wording
  "You can see the status here".
- The Send Campaign admin endpoint surfaces a redirect URL pointing at
  /studio/worker/ in its JSON response and sets the matching flash.
- The legacy global worker-down banner (red bar across every studio page)
  is gone and the ``worker_status_banner`` context processor is unwired.
"""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from email_app.models import EmailCampaign
from integrations.models import ContentSource
from studio.tests.test_worker_health import _fake_cluster

User = get_user_model()


class GlobalBannerRemovedTest(TestCase):
    """The studio-wide red banner is no longer rendered, ever."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_no_global_banner_on_dashboard_when_worker_down(self):
        """The full-width red ``Background worker not running`` banner
        from the old design must not render anywhere."""
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Background worker not running')

    def test_no_global_banner_on_sync_page_when_worker_down(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Background worker not running')

    def test_worker_status_banner_context_processor_is_unwired(self):
        """The studio_worker_status context variable should no longer be
        populated globally — the inline indicator computes its own."""
        response = self.client.get('/studio/')
        self.assertNotIn('studio_worker_status', response.context)


class DashboardWorkerPanelTest(TestCase):
    """The studio dashboard shows a prominent worker status panel."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_panel_shows_running_when_cluster_alive(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="dashboard-worker-panel"')
        self.assertContains(response, 'Worker running')

    def test_panel_shows_not_running_when_no_cluster(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="dashboard-worker-panel"')
        self.assertContains(response, 'Worker not running')
        self.assertContains(response, 'manage.py qcluster')

    def test_panel_links_to_worker_dashboard(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/')
        # Anchor that takes the operator to the worker page.
        self.assertContains(response, 'href="/studio/worker/"')

    def test_panel_hidden_when_expect_worker_false(self):
        """If the deployment doesn't expect a worker, suppress the panel."""
        with patch('studio.worker_health.Stat.get_all', return_value=[]), \
             patch.dict(os.environ, {'EXPECT_WORKER': 'false'}):
            response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="dashboard-worker-panel"')


class InlineIndicatorPresenceTest(TestCase):
    """The subtle inline indicator must appear on every page that submits
    work to the queue, in both states."""

    INDICATOR_PAGES = [
        '/studio/sync/',
        '/studio/sync/history/',
        '/studio/campaigns/',
        '/studio/campaigns/new',
        '/studio/notifications/',
    ]

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.campaign = EmailCampaign.objects.create(
            subject='Test', body='Body', target_min_level=0, status='draft',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_indicator_shows_running_state_with_queue_depth(self):
        for path in self.INDICATOR_PAGES + [f'/studio/campaigns/{self.campaign.pk}/']:
            with self.subTest(path=path), \
                 patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertContains(response, 'data-testid="worker-status-inline"')
                self.assertContains(response, 'Running')
                # Queue depth label is part of the running variant.
                self.assertContains(response, 'Queue: ')

    def test_indicator_shows_not_running_state(self):
        for path in self.INDICATOR_PAGES + [f'/studio/campaigns/{self.campaign.pk}/']:
            with self.subTest(path=path), \
                 patch('studio.worker_health.Stat.get_all', return_value=[]):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertContains(response, 'data-testid="worker-status-inline"')
                self.assertContains(response, 'Not running')

    def test_indicator_links_to_worker_dashboard(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.get('/studio/sync/')
        self.assertContains(response, 'href="/studio/worker/"')

    def test_indicator_suppressed_when_expect_worker_false(self):
        """``EXPECT_WORKER=false`` (one-off scripts, dev environments without
        a worker) should hide the indicator entirely so it doesn't shout."""
        with patch('studio.worker_health.Stat.get_all', return_value=[]), \
             patch.dict(os.environ, {'EXPECT_WORKER': 'false'}):
            response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="worker-status-inline"')


class SyncSubmitRedirectTest(TestCase):
    """Sync Now / Sync All must enqueue the job, then redirect back to the
    sync dashboard (NOT the worker page — see #239) with a flash message
    that includes a clickable link to the worker page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_sync_trigger_redirects_to_sync_dashboard(self, _mock_async):
        """Per #239 the operator stays on the sync dashboard so they can
        keep reading sync history instead of being yanked to /worker/."""
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_sync_trigger_flash_mentions_source_and_links_to_worker(self, _mock_async):
        """The flash mentions the specific source and includes a clickable
        ``here`` link to /studio/worker/ for operators who want to watch."""
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            response = self.client.post(
                f'/studio/sync/{self.source.pk}/trigger/',
                follow=True,
            )
        body = response.content.decode()
        self.assertIn('Sync queued for AI-Shipping-Labs/blog', body)
        self.assertIn(
            'You can see the status <a href="/studio/worker/" class="underline">here</a>',
            body,
        )

    @patch('django_q.tasks.async_task')
    def test_sync_trigger_flash_warns_when_worker_down(self, _mock_async):
        """The worker-down warning suffix is preserved on the flash and
        the ``here`` link still renders."""
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            response = self.client.post(
                f'/studio/sync/{self.source.pk}/trigger/',
                follow=True,
            )
        body = response.content.decode()
        self.assertIn('worker is not running', body)
        self.assertIn('manage.py qcluster', body)
        self.assertIn('href="/studio/worker/"', body)

    @patch('django_q.tasks.async_task')
    def test_sync_all_redirects_to_sync_dashboard(self, _mock_async):
        """Per #239 Sync All also stays on the sync dashboard so the
        operator can watch every per-source row update in place."""
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_sync_all_flash_mentions_count_and_links_to_worker(self, _mock_async):
        # Issue #310: one source per repo. Use distinct repo_names.
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
        )
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            response = self.client.post('/studio/sync/all/', follow=True)
        body = response.content.decode()
        # 1 source from setUpTestData + 2 created above = 3 total.
        self.assertIn('Sync queued for 3 sources', body)
        self.assertIn(
            'You can see the status <a href="/studio/worker/" class="underline">here</a>',
            body,
        )


class SendCampaignRedirectTest(TestCase):
    """The admin Send Campaign endpoint returns a redirect URL pointing at
    the worker dashboard so the JS handler can navigate the user there."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        cls.campaign = EmailCampaign.objects.create(
            subject='My Campaign',
            body='# Hi',
            target_min_level=0,
            status='draft',
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    @patch('jobs.tasks.async_task', return_value='task-123')
    def test_send_campaign_response_includes_worker_redirect_url(self, _mock):
        url = reverse(
            'admin:email_app_emailcampaign_send_campaign',
            args=[self.campaign.pk],
        )
        response = self.client.post(url, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['redirect_url'], '/studio/worker/')

    @patch('jobs.tasks.async_task', return_value='task-123')
    def test_send_campaign_message_explains_redirect(self, _mock):
        url = reverse(
            'admin:email_app_emailcampaign_send_campaign',
            args=[self.campaign.pk],
        )
        response = self.client.post(url, content_type='application/json')
        data = response.json()
        self.assertIn('queued for sending', data['message'])
        self.assertIn('watching it here', data['message'])
