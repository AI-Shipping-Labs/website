"""The GitHub commit resolver for the project submit form (HTTP mocked)."""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from content.services import github_commit
from content.services.github_commit import (
    GitHubUnavailable,
    InvalidRepositoryUrl,
    RepositoryNotFound,
    github_commit_url,
    parse_github_url,
    resolve_commit,
)

SHA = 'f817e851c9287a8a023093306ef12dc178c2b6b9'
GET = 'content.services.github_commit.requests.get'


def _response(status=200, body=None, headers=None):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.json.return_value = body if body is not None else {'sha': SHA}
    return response


class ParseGithubUrlTest(SimpleTestCase):
    def test_plain_repo_resolves_head(self):
        self.assertEqual(
            parse_github_url('https://github.com/acme/app'), ('acme', 'app', 'HEAD'),
        )

    def test_git_suffix_and_trailing_slash(self):
        self.assertEqual(
            parse_github_url('https://github.com/acme/app.git/'),
            ('acme', 'app', 'HEAD'),
        )

    def test_tree_ref_with_slash_keeps_whole_ref(self):
        self.assertEqual(
            parse_github_url('https://github.com/acme/app/tree/feat/x'),
            ('acme', 'app', 'feat/x'),
        )

    def test_commit_link_uses_sha(self):
        self.assertEqual(
            parse_github_url('https://github.com/acme/app/commit/abc1234'),
            ('acme', 'app', 'abc1234'),
        )

    def test_non_github_and_incomplete_links_rejected(self):
        for url in (
            'https://example.com/acme/app', 'https://github.com/acme', '',
            'ftp://github.com/acme/app', 'https://notgithub.com/acme/app',
        ):
            with self.subTest(url=url), self.assertRaises(InvalidRepositoryUrl):
                parse_github_url(url)


@patch.object(github_commit, '_auth_headers', return_value={})
class ResolveCommitTest(SimpleTestCase):
    def test_returns_full_sha_and_calls_commits_endpoint_with_timeout(self, _auth):
        with patch(GET, return_value=_response()) as get:
            self.assertEqual(resolve_commit('https://github.com/acme/app'), SHA)
        self.assertEqual(
            get.call_args.args[0],
            'https://api.github.com/repos/acme/app/commits/HEAD',
        )
        self.assertLessEqual(get.call_args.kwargs['timeout'], 5)

    def test_tree_ref_is_requested(self, _auth):
        with patch(GET, return_value=_response()) as get:
            resolve_commit('https://github.com/acme/app/tree/dev')
        self.assertTrue(get.call_args.args[0].endswith('/commits/dev'))

    def test_404_means_not_public(self, _auth):
        with patch(GET, return_value=_response(404)), self.assertRaises(RepositoryNotFound):
            resolve_commit('https://github.com/acme/app')

    def test_rate_limit_is_unavailable(self, _auth):
        limited = _response(403, headers={'X-RateLimit-Remaining': '0'})
        with patch(GET, return_value=limited), self.assertRaises(GitHubUnavailable):
            resolve_commit('https://github.com/acme/app')

    def test_server_error_is_unavailable(self, _auth):
        with patch(GET, return_value=_response(502)), self.assertRaises(GitHubUnavailable):
            resolve_commit('https://github.com/acme/app')

    def test_timeout_is_unavailable(self, _auth):
        with patch(GET, side_effect=requests.Timeout()), self.assertRaises(GitHubUnavailable):
            resolve_commit('https://github.com/acme/app')

    def test_malformed_body_is_unavailable(self, _auth):
        with patch(GET, return_value=_response(body={'sha': 'nope'})), self.assertRaises(GitHubUnavailable):
            resolve_commit('https://github.com/acme/app')


class GithubCommitUrlTest(SimpleTestCase):
    def test_normalizes_repo_links(self):
        expected = f'https://github.com/o/r/commit/{SHA}'
        for link in (
            'https://github.com/o/r',
            'https://github.com/o/r/',
            'https://github.com/o/r.git',
            'https://github.com/o/r/tree/main',
            'https://www.github.com/o/r/tree/feature/x',
        ):
            with self.subTest(link=link):
                self.assertEqual(github_commit_url(link, SHA), expected)

    def test_unparseable_or_missing_returns_empty(self):
        self.assertEqual(github_commit_url('https://gitlab.com/o/r', SHA), '')
        self.assertEqual(github_commit_url('not a url', SHA), '')
        self.assertEqual(github_commit_url('https://github.com/o/r', ''), '')
