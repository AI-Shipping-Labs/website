"""Tests for the GitHub App ``list_installation_repositories`` helper.

Covers issue #199: Studio repo dropdown when registering a new content source.
"""

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from integrations.services.github import (
    INSTALLATION_REPOS_CACHE_KEY,
    GitHubSyncError,
    clear_installation_repositories_cache,
    list_installation_repositories,
)


def _mock_repos_response(repos, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {'repositories': repos}
    response.text = ''
    return response


@override_settings(
    GITHUB_APP_ID='12345',
    GITHUB_APP_PRIVATE_KEY='fake-key',
    GITHUB_APP_INSTALLATION_ID='67890',
)
class ListInstallationRepositoriesTest(TestCase):
    """Verify the repo-list helper used by the Studio dropdown."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_returns_full_name_private_default_branch(self, mock_get, mock_token):
        mock_get.return_value = _mock_repos_response([
            {
                'full_name': 'AI-Shipping-Labs/content',
                'private': True,
                'default_branch': 'main',
            },
            {
                'full_name': 'AI-Shipping-Labs/blog',
                'private': False,
                'default_branch': 'master',
            },
        ])

        repos = list_installation_repositories()

        # Sorted alphabetically (case-insensitive)
        self.assertEqual(
            [r['full_name'] for r in repos],
            ['AI-Shipping-Labs/blog', 'AI-Shipping-Labs/content'],
        )
        blog = repos[0]
        self.assertFalse(blog['private'])
        self.assertEqual(blog['default_branch'], 'master')
        content = repos[1]
        self.assertTrue(content['private'])
        self.assertEqual(content['default_branch'], 'main')

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_uses_token_in_authorization_header(self, mock_get, mock_token):
        mock_get.return_value = _mock_repos_response([])
        list_installation_repositories()

        call = mock_get.call_args
        headers = call.kwargs.get('headers') or call[1].get('headers')
        self.assertEqual(headers['Authorization'], 'token tok')
        self.assertIn('installation/repositories', call.args[0])

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_caches_result_for_subsequent_calls(self, mock_get, mock_token):
        mock_get.return_value = _mock_repos_response([
            {'full_name': 'x/y', 'private': False, 'default_branch': 'main'},
        ])

        list_installation_repositories()
        list_installation_repositories()
        list_installation_repositories()

        # GitHub API should only be hit once thanks to the cache
        self.assertEqual(mock_get.call_count, 1)

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_force_refresh_bypasses_cache(self, mock_get, mock_token):
        mock_get.return_value = _mock_repos_response([
            {'full_name': 'x/y', 'private': False, 'default_branch': 'main'},
        ])

        list_installation_repositories()
        list_installation_repositories(force_refresh=True)

        self.assertEqual(mock_get.call_count, 2)

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_clear_cache_helper_drops_entry(self, mock_get, mock_token):
        mock_get.return_value = _mock_repos_response([
            {'full_name': 'x/y', 'private': False, 'default_branch': 'main'},
        ])
        list_installation_repositories()
        self.assertIsNotNone(cache.get(INSTALLATION_REPOS_CACHE_KEY))

        clear_installation_repositories_cache()

        self.assertIsNone(cache.get(INSTALLATION_REPOS_CACHE_KEY))

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_paginates_when_full_page_returned(self, mock_get, mock_token):
        # First page returns a full 100 entries -> helper requests page 2
        page1_repos = [
            {'full_name': f'org/repo{i:03d}', 'private': False,
             'default_branch': 'main'}
            for i in range(100)
        ]
        page2_repos = [
            {'full_name': 'org/last', 'private': True,
             'default_branch': 'main'},
        ]
        mock_get.side_effect = [
            _mock_repos_response(page1_repos),
            _mock_repos_response(page2_repos),
        ]

        repos = list_installation_repositories()

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(len(repos), 101)

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_raises_github_sync_error_on_non_200(self, mock_get, mock_token):
        response = MagicMock()
        response.status_code = 401
        response.text = 'Bad credentials'
        mock_get.return_value = response

        with self.assertRaises(GitHubSyncError) as ctx:
            list_installation_repositories()
        self.assertIn('401', str(ctx.exception))

    @override_settings(
        GITHUB_APP_ID='',
        GITHUB_APP_PRIVATE_KEY='',
        GITHUB_APP_INSTALLATION_ID='',
    )
    def test_missing_credentials_propagates_error(self):
        with self.assertRaises(GitHubSyncError):
            list_installation_repositories()

    @patch('integrations.services.github_sync.client.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github_sync.client.requests.get')
    def test_handles_empty_repository_list(self, mock_get, mock_token):
        mock_get.return_value = _mock_repos_response([])
        repos = list_installation_repositories()
        self.assertEqual(repos, [])
