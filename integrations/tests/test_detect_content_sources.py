"""Tests for ``detect_content_sources`` (issue #213).

Auto-detects which ``ContentSource`` rows we should create for a given repo
by inspecting the repo via the GitHub Trees + Contents APIs. The helper is
mocked at the requests layer so tests do not hit the network.
"""

import base64
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from integrations.services.github import (
    DETECT_CONTENT_CACHE_KEY_PREFIX,
    GitHubSyncError,
    clear_detect_content_sources_cache,
    detect_content_sources,
)


def _b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


def _mock_response(status_code, json_payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_payload or {}
    response.text = ''
    return response


def _make_request_router(repo_meta, tree_entries, file_contents):
    """Return a ``requests.get`` side-effect mapping URL prefixes to responses.

    - ``GET /repos/{owner}/{repo}`` -> ``repo_meta``
    - ``GET /repos/{owner}/{repo}/git/trees/{branch}`` -> ``{'tree': tree_entries}``
    - ``GET /repos/{owner}/{repo}/contents/{path}`` -> base64 of
      ``file_contents.get(path)`` or 404 if not present.
    """
    def _router(url, **_kwargs):
        if '/git/trees/' in url:
            return _mock_response(200, {'tree': tree_entries})
        if '/contents/' in url:
            path = url.split('/contents/', 1)[1]
            text = file_contents.get(path)
            if text is None:
                return _mock_response(404)
            return _mock_response(200, {
                'encoding': 'base64',
                'content': _b64(text),
            })
        # Plain repo metadata URL
        return _mock_response(200, repo_meta)

    return _router


@override_settings(
    GITHUB_APP_ID='12345',
    GITHUB_APP_PRIVATE_KEY='fake-key',
    GITHUB_APP_INSTALLATION_ID='67890',
)
class DetectContentSourcesTest(TestCase):
    """Verify the auto-detection rules listed in issue #213."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    # -- detection rules (one per content type) ------------------------------

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_root_course_yaml(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [{'type': 'blob', 'path': 'course.yaml'}]
        mock_get.side_effect = _make_request_router(repo_meta, tree, {})

        results = detect_content_sources('org/python-course')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['content_type'], 'course')
        self.assertEqual(results[0]['content_path'], '')

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_multi_course_directory(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [
            {'type': 'blob', 'path': 'courses/foo/course.yaml'},
            {'type': 'blob', 'path': 'courses/bar/course.yaml'},
        ]
        mock_get.side_effect = _make_request_router(repo_meta, tree, {})

        results = detect_content_sources('org/multi')

        # Both nested course.yaml files collapse into a single course source
        # at the parent dir ``courses``.
        course_results = [r for r in results if r['content_type'] == 'course']
        self.assertEqual(len(course_results), 1)
        self.assertEqual(course_results[0]['content_path'], 'courses')

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_event_yaml_with_start_datetime(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [
            {'type': 'blob', 'path': 'events/kickoff.yaml'},
        ]
        files = {
            'events/kickoff.yaml': (
                'title: Kickoff\n'
                'start_datetime: 2026-04-17T18:00:00Z\n'
            ),
        }
        mock_get.side_effect = _make_request_router(repo_meta, tree, files)

        results = detect_content_sources('org/events')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['content_type'], 'event')
        self.assertEqual(results[0]['content_path'], 'events')

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_project_markdown_with_difficulty_and_author(
        self, mock_get, _mock_token,
    ):
        repo_meta = {'default_branch': 'main'}
        tree = [
            {'type': 'blob', 'path': 'projects/llm-rag.md'},
        ]
        files = {
            'projects/llm-rag.md': (
                '---\n'
                'title: LLM RAG\n'
                'difficulty: intermediate\n'
                'author: Alice\n'
                'date: 2026-01-15\n'
                '---\n'
                'body\n'
            ),
        }
        mock_get.side_effect = _make_request_router(repo_meta, tree, files)

        results = detect_content_sources('org/projects')

        # The file has BOTH project signals and a date, but project takes
        # priority -- it should not be double-claimed as an article.
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['content_type'], 'project')
        self.assertEqual(results[0]['content_path'], 'projects')

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_article_markdown_with_date(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [
            {'type': 'blob', 'path': 'blog/hello.md'},
        ]
        files = {
            'blog/hello.md': (
                '---\n'
                'title: Hello\n'
                'date: 2026-04-17\n'
                '---\n'
                'body\n'
            ),
        }
        mock_get.side_effect = _make_request_router(repo_meta, tree, files)

        results = detect_content_sources('org/blog')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['content_type'], 'article')
        self.assertEqual(results[0]['content_path'], 'blog')

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_interview_question_root_md(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        # Root-level .md files that look like topic names. README is excluded.
        tree = [
            {'type': 'blob', 'path': 'python.md'},
            {'type': 'blob', 'path': 'machine-learning.md'},
            {'type': 'blob', 'path': 'README.md'},
        ]
        # No frontmatter -> not an article, not a project -> falls through.
        files = {
            'python.md': '# Python questions\n',
            'machine-learning.md': '# ML questions\n',
        }
        mock_get.side_effect = _make_request_router(repo_meta, tree, files)

        results = detect_content_sources('org/interview-questions')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['content_type'], 'interview_question')
        self.assertEqual(results[0]['content_path'], '')

    # -- monorepo: multiple types in one repo --------------------------------

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_detects_monorepo_with_courses_articles_and_events(
        self, mock_get, _mock_token,
    ):
        repo_meta = {'default_branch': 'main'}
        tree = [
            {'type': 'blob', 'path': 'courses/foo/course.yaml'},
            {'type': 'blob', 'path': 'blog/hello.md'},
            {'type': 'blob', 'path': 'events/kickoff.yaml'},
        ]
        files = {
            'blog/hello.md': '---\ntitle: Hello\ndate: 2026-04-17\n---\nbody\n',
            'events/kickoff.yaml': (
                'title: Kickoff\nstart_datetime: 2026-04-17T18:00:00Z\n'
            ),
        }
        mock_get.side_effect = _make_request_router(repo_meta, tree, files)

        results = detect_content_sources('org/content')

        keys = {(r['content_type'], r['content_path']) for r in results}
        self.assertIn(('course', 'courses'), keys)
        self.assertIn(('article', 'blog'), keys)
        self.assertIn(('event', 'events'), keys)

    # -- nothing matched -----------------------------------------------------

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_returns_empty_list_when_no_signals_present(
        self, mock_get, _mock_token,
    ):
        repo_meta = {'default_branch': 'main'}
        tree = [
            # No course.yaml, no markdown, no YAML at root.
            {'type': 'blob', 'path': 'src/index.js'},
            {'type': 'blob', 'path': 'package.json'},
            {'type': 'blob', 'path': 'README.md'},  # excluded by name
        ]
        mock_get.side_effect = _make_request_router(repo_meta, tree, {})

        results = detect_content_sources('org/random-repo')

        self.assertEqual(results, [])

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_markdown_without_recognised_frontmatter_is_ignored(
        self, mock_get, _mock_token,
    ):
        repo_meta = {'default_branch': 'main'}
        tree = [
            {'type': 'blob', 'path': 'docs/intro.md'},
        ]
        files = {
            'docs/intro.md': '# Intro\n\nNo frontmatter here.\n',
        }
        mock_get.side_effect = _make_request_router(repo_meta, tree, files)

        results = detect_content_sources('org/docs-only')

        self.assertEqual(results, [])

    # -- error propagation ---------------------------------------------------

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_raises_github_sync_error_when_repo_metadata_404s(
        self, mock_get, _mock_token,
    ):
        def _router(url, **_kwargs):
            return _mock_response(404)
        mock_get.side_effect = _router

        with self.assertRaises(GitHubSyncError):
            detect_content_sources('org/missing')

    # -- caching -------------------------------------------------------------

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_caches_results_per_repo(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [{'type': 'blob', 'path': 'course.yaml'}]
        mock_get.side_effect = _make_request_router(repo_meta, tree, {})

        detect_content_sources('org/python-course')
        first_call_count = mock_get.call_count
        detect_content_sources('org/python-course')

        # Second call did not hit GitHub at all.
        self.assertEqual(mock_get.call_count, first_call_count)

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_force_refresh_bypasses_cache(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [{'type': 'blob', 'path': 'course.yaml'}]
        mock_get.side_effect = _make_request_router(repo_meta, tree, {})

        detect_content_sources('org/python-course')
        first_count = mock_get.call_count
        detect_content_sources('org/python-course', force_refresh=True)

        self.assertGreater(mock_get.call_count, first_count)

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_clear_cache_drops_specific_entry(self, mock_get, _mock_token):
        repo_meta = {'default_branch': 'main'}
        tree = [{'type': 'blob', 'path': 'course.yaml'}]
        mock_get.side_effect = _make_request_router(repo_meta, tree, {})

        detect_content_sources('org/python-course')
        self.assertIsNotNone(cache.get(
            f'{DETECT_CONTENT_CACHE_KEY_PREFIX}org/python-course',
        ))

        clear_detect_content_sources_cache('org/python-course')

        self.assertIsNone(cache.get(
            f'{DETECT_CONTENT_CACHE_KEY_PREFIX}org/python-course',
        ))
