"""Tests for the Studio "Add content source" form (issue #310).

The form is now single-click: pick a repo, submit, get a flash with the
webhook secret. The historic confirm step and one-shot "created" page
are gone.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from integrations.models import ContentSource, SyncLog
from integrations.services.github import (
    INSTALLATION_REPOS_CACHE_KEY,
    GitHubSyncError,
)
from jobs.tasks import build_task_name

User = get_user_model()


SAMPLE_REPOS = [
    {
        'full_name': 'AI-Shipping-Labs/blog',
        'private': False,
        'default_branch': 'main',
    },
    {
        'full_name': 'AI-Shipping-Labs/content',
        'private': True,
        'default_branch': 'main',
    },
]


class ContentSourceCreateViewTest(TestCase):
    """The Studio form to register a new ContentSource via the GitHub App."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def tearDown(self):
        cache.clear()

    # -- access control ------------------------------------------------------

    def test_anonymous_redirected_to_login(self):
        client = Client()
        response = client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_user_gets_403(self):
        User.objects.create_user(
            email='member@test.com', password='testpass', is_staff=False,
        )
        client = Client()
        client.login(email='member@test.com', password='testpass')
        response = client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 403)

    # -- GET: rendering ------------------------------------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_renders_form_with_repo_dropdown(self, _mock_list):
        response = self.client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/content_sources/create.html')
        self.assertContains(response, 'value="AI-Shipping-Labs/blog"')
        self.assertContains(response, 'value="AI-Shipping-Labs/content"')

    @patch('studio.views.content_sources.list_installation_repositories',
           side_effect=GitHubSyncError('GitHub App credentials not configured.'))
    def test_get_shows_error_when_github_not_configured(self, _mock_list):
        with self.assertLogs('studio.views.content_sources', level='WARNING') as logs:
            response = self.client.get('/studio/content-sources/new/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'Could not fetch installation repositories: '
            'GitHub App credentials not configured.',
            logs.output[0],
        )
        self.assertContains(response, 'GitHub App credentials not configured')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_marks_private_repos_in_label(self, _mock_list):
        response = self.client.get('/studio/content-sources/new/')
        self.assertContains(response, 'AI-Shipping-Labs/content (private)')

    # -- GET: filtering already-registered repos -----------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_hides_repos_with_existing_content_source(self, _mock_list):
        ContentSource.objects.create(repo_name='AI-Shipping-Labs/blog')
        response = self.client.get('/studio/content-sources/new/')
        self.assertNotContains(response, 'value="AI-Shipping-Labs/blog"')
        self.assertContains(response, 'value="AI-Shipping-Labs/content"')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_get_shows_empty_state_when_all_repos_registered(self, _mock_list):
        ContentSource.objects.create(repo_name='AI-Shipping-Labs/blog')
        ContentSource.objects.create(repo_name='AI-Shipping-Labs/content')
        response = self.client.get('/studio/content-sources/new/')
        self.assertContains(
            response, 'All accessible repos are already registered',
        )

    # -- POST: single-click create ------------------------------------------

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_creates_single_source_and_flashes_secret(
            self, _mock_list, mock_async):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'manual-secret',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertEqual(source.webhook_secret, 'manual-secret')
        self.assertFalse(source.is_private)
        self.assertEqual(source.last_sync_status, 'queued')

        mock_async.assert_called_once_with(
            'integrations.services.github.sync_content_source',
            source,
            task_name=build_task_name(
                'Sync content source',
                source.repo_name,
                'Studio content source create',
            ),
        )
        self.assertEqual(
            SyncLog.objects.filter(source=source, status='queued').count(),
            1,
        )

        # Follow the redirect; the flash on the dashboard renders the secret.
        followed = self.client.get('/studio/sync/')
        self.assertContains(followed, 'manual-secret')
        self.assertContains(followed, 'First sync queued')
        self.assertContains(followed, '/studio/worker/')

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_auto_generates_webhook_secret_when_blank(
            self, _mock_list, _mock_async):
        self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertTrue(source.webhook_secret)
        self.assertGreaterEqual(len(source.webhook_secret), 32)

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_takes_is_private_from_github_api(self, _mock_list, _mock_async):
        self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/content',
            'webhook_secret': '',
        })
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/content')
        self.assertTrue(source.is_private)

    @patch('django_q.tasks.async_task')
    @patch('studio.views.sync.get_worker_status',
           return_value={'expect_worker': True, 'alive': False})
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_warns_when_worker_is_down(
            self, _mock_list, _mock_worker, _mock_async):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'manual-secret',
        }, follow=True)
        body = response.content.decode()
        self.assertIn('First sync queued', body)
        self.assertIn('worker is not running', body)
        self.assertIn('manage.py qcluster', body)
        self.assertIn('/studio/worker/', body)

    @patch('django_q.tasks.async_task', side_effect=RuntimeError('queue offline'))
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_keeps_source_without_queued_state_when_enqueue_fails(
            self, _mock_list, _mock_async):
        with self.assertLogs('studio.views.content_sources', level='ERROR') as logs:
            response = self.client.post('/studio/content-sources/new/', {
                'repo_name': 'AI-Shipping-Labs/blog',
                'webhook_secret': 'manual-secret',
            }, follow=True)

        self.assertEqual(response.redirect_chain[-1], ('/studio/sync/', 302))
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertIsNone(source.last_sync_status)
        self.assertFalse(SyncLog.objects.filter(source=source).exists())
        self.assertIn(
            'Could not queue initial sync for AI-Shipping-Labs/blog',
            logs.output[0],
        )
        body = response.content.decode()
        self.assertIn('The first sync could not be queued', body)
        self.assertIn('use Sync now', body)

    @patch('django_q.tasks.async_task', side_effect=ImportError)
    @patch('studio.views.content_sources.sync_content_source')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_runs_sync_inline_when_django_q_unavailable(
            self, _mock_list, mock_sync, _mock_async):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'manual-secret',
        }, follow=True)

        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        mock_sync.assert_called_once_with(source)
        self.assertFalse(SyncLog.objects.filter(source=source).exists())
        self.assertContains(response, 'First sync completed')

    # -- POST: validation ----------------------------------------------------

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_repo_not_in_installation(self, _mock_list, mock_async):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': 'evil-org/private-stuff',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContentSource.objects.filter(
            repo_name='evil-org/private-stuff',
        ).exists())
        self.assertContains(
            response,
            'not accessible to the GitHub App installation',
            status_code=400,
        )
        mock_async.assert_not_called()
        self.assertFalse(SyncLog.objects.exists())

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_missing_repo(self, _mock_list, mock_async):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': '',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContentSource.objects.exists())
        mock_async.assert_not_called()
        self.assertFalse(SyncLog.objects.exists())

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_repo_already_registered(self, _mock_list, mock_async):
        ContentSource.objects.create(repo_name='AI-Shipping-Labs/blog')
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already registered', status_code=400)
        self.assertEqual(
            ContentSource.objects.filter(
                repo_name='AI-Shipping-Labs/blog',
            ).count(),
            1,
        )
        mock_async.assert_not_called()
        self.assertFalse(SyncLog.objects.exists())

    @patch('django_q.tasks.async_task')
    @patch('studio.views.content_sources.list_installation_repositories',
           side_effect=GitHubSyncError('boom'))
    def test_post_rejects_when_github_api_unreachable(
            self, _mock_list, mock_async):
        with self.assertLogs('studio.views.content_sources', level='WARNING') as logs:
            response = self.client.post('/studio/content-sources/new/', {
                'repo_name': 'AI-Shipping-Labs/blog',
                'webhook_secret': '',
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'Could not fetch installation repositories: boom',
            logs.output[0],
        )
        self.assertFalse(ContentSource.objects.exists())
        mock_async.assert_not_called()
        self.assertFalse(SyncLog.objects.exists())


class ContentSourceRefreshViewTest(TestCase):
    """The "Refresh repo list" button drops the cached entry."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def tearDown(self):
        cache.clear()

    def test_refresh_clears_cache_and_redirects_to_form(self):
        cache.set(INSTALLATION_REPOS_CACHE_KEY, [{'full_name': 'cached/repo'}])
        response = self.client.post('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/content-sources/new/')
        self.assertIsNone(cache.get(INSTALLATION_REPOS_CACHE_KEY))

    def test_refresh_requires_post(self):
        response = self.client.get('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 405)

    def test_refresh_requires_staff(self):
        client = Client()
        response = client.post('/studio/content-sources/refresh/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])


class SyncDashboardLinksToCreateTest(TestCase):
    """The sync dashboard exposes the "Add content source" entry point."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_has_link_to_add_form(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '/studio/content-sources/new/')
