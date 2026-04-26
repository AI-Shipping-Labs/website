"""Tests for the watch_content management command helpers.

Issue #310: with one ContentSource per repo, the watcher's path-mapping
helpers (``_build_path_mapping``, ``_get_content_type_for_path``) are
gone — replaced by a single per-repo debounced sync. The remaining
helper is ``_is_content_file`` plus ``DebouncedSyncer``.
"""

import threading
from unittest.mock import MagicMock, patch

from django.test import TestCase

from integrations.management.commands.watch_content import (
    TIERS_SENTINEL,
    DebouncedSyncer,
    _is_content_file,
)
from integrations.models import ContentSource


class IsContentFileTest(TestCase):
    """``_is_content_file`` recognizes content files."""

    def test_md_file_is_content(self):
        self.assertTrue(_is_content_file('blog/article.md'))

    def test_yaml_file_is_content(self):
        self.assertTrue(_is_content_file('events/event.yaml'))

    def test_yml_file_is_content(self):
        self.assertTrue(_is_content_file('config.yml'))

    def test_dotfile_excluded(self):
        self.assertFalse(_is_content_file('.gitignore'))

    def test_dotdir_excluded(self):
        self.assertFalse(_is_content_file('.git/HEAD'))

    def test_swap_file_excluded(self):
        self.assertFalse(_is_content_file('blog/article.md.swp'))

    def test_temp_file_excluded(self):
        self.assertFalse(_is_content_file('blog/article.md.tmp'))

    def test_py_file_excluded(self):
        self.assertFalse(_is_content_file('script.py'))


class DebouncedSyncerTest(TestCase):
    """``DebouncedSyncer`` collects events and fires after the debounce."""

    @classmethod
    def setUpTestData(cls):
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def _make_syncer(self, debounce=0.05):
        return DebouncedSyncer(
            debounce_seconds=debounce,
            repo_dir='/tmp/x',
            stdout=MagicMock(),
            stderr=MagicMock(),
            style=MagicMock(write=lambda x: x, NOTICE=lambda x: x, SUCCESS=lambda x: x),
        )

    @patch('integrations.management.commands.watch_content.sync_content_source')
    def test_schedule_then_fire_calls_sync(self, mock_sync):
        syncer = self._make_syncer(debounce=0.05)
        done = threading.Event()
        original = syncer._sync_content_source

        def wrapper(s):
            try:
                original(s)
            finally:
                done.set()

        syncer._sync_content_source = wrapper
        syncer.schedule(self.source.repo_name, self.source)
        done.wait(timeout=2.0)
        mock_sync.assert_called_once()
        # First positional arg is the source.
        self.assertEqual(mock_sync.call_args.args[0], self.source)

    def test_tiers_sentinel_routes_to_tiers_sync(self):
        syncer = self._make_syncer(debounce=0.05)
        done = threading.Event()
        called = []

        def wrapper():
            try:
                called.append(True)
            finally:
                done.set()

        syncer._sync_tiers = wrapper
        syncer.schedule(TIERS_SENTINEL, TIERS_SENTINEL)
        done.wait(timeout=2.0)
        self.assertEqual(called, [True])
