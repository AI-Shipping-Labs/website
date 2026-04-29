"""Tests for the Studio "Add content source" form (issue #310).

The form is now single-click: pick a repo, submit, get a flash with the
webhook secret. The historic confirm step and one-shot "created" page
are gone.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from integrations.models import ContentSource
from integrations.services.github import (
    INSTALLATION_REPOS_CACHE_KEY,
    GitHubSyncError,
)

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

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_creates_single_source_and_flashes_secret(self, _mock_list):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': 'manual-secret',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertEqual(source.webhook_secret, 'manual-secret')
        self.assertFalse(source.is_private)

        # Follow the redirect; the flash on the dashboard renders the secret.
        followed = self.client.get('/studio/sync/')
        self.assertContains(followed, 'manual-secret')

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_auto_generates_webhook_secret_when_blank(self, _mock_list):
        self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/blog',
            'webhook_secret': '',
        })
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog')
        self.assertTrue(source.webhook_secret)
        self.assertGreaterEqual(len(source.webhook_secret), 32)

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_takes_is_private_from_github_api(self, _mock_list):
        self.client.post('/studio/content-sources/new/', {
            'repo_name': 'AI-Shipping-Labs/content',
            'webhook_secret': '',
        })
        source = ContentSource.objects.get(repo_name='AI-Shipping-Labs/content')
        self.assertTrue(source.is_private)

    # -- POST: validation ----------------------------------------------------

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_repo_not_in_installation(self, _mock_list):
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

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_missing_repo(self, _mock_list):
        response = self.client.post('/studio/content-sources/new/', {
            'repo_name': '',
            'webhook_secret': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContentSource.objects.exists())

    @patch('studio.views.content_sources.list_installation_repositories',
           return_value=SAMPLE_REPOS)
    def test_post_rejects_repo_already_registered(self, _mock_list):
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

    @patch('studio.views.content_sources.list_installation_repositories',
           side_effect=GitHubSyncError('boom'))
    def test_post_rejects_when_github_api_unreachable(self, _mock_list):
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
