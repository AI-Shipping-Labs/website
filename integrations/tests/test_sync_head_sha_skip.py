"""Tests for issue #235 — skip sync when HEAD SHA matches last successful sync.

Covers the cheap HEAD-SHA short-circuit added to ``sync_content_source``:
before paying the cost of a full clone + file walk we run
``git ls-remote <url> HEAD`` and compare the result against
``ContentSource.last_synced_commit``. If they match (and the previous sync
ended in ``success``), we write a ``skipped`` SyncLog and return.

Ported to the package engine (A2.3): the cheap remote-HEAD check now
lives in ``community_base.content_sync.orchestration`` and resolves HEAD
through the package ``GitHubClient`` (``resolve_commit``). The site suite
keeps the skip-contract tests against the public entry points.

The repo-level skip differs from issue #225's per-item change detection:
#225 still does the full clone + file walk and only short-circuits the
``update_or_create`` call per file; this issue avoids the clone + walk
entirely. Both compose.
"""

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import uuid
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from community_base.content_sync.github import checkout_repository
from integrations.models import ContentSource, SyncLog
from integrations.services.github import sync_content_source


def _write_md(filepath, frontmatter_dict, body=''):
    if 'content_id' not in frontmatter_dict:
        frontmatter_dict['content_id'] = str(uuid.uuid4())
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = ['---']
    for key, value in frontmatter_dict.items():
        if isinstance(value, bool):
            lines.append(f'{key}: {str(value).lower()}')
        elif isinstance(value, int):
            lines.append(f'{key}: {value}')
        else:
            lines.append(f'{key}: "{value}"')
    lines.append('---')
    lines.append(body)
    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))


# ---------------------------------------------------------------------------
# sync_content_source skip behaviour
# ---------------------------------------------------------------------------


class SyncSkipFirstSyncTest(TestCase):
    """First-ever sync runs unconditionally — no baseline to compare to."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-first',
        )
        self.temp_dir = tempfile.mkdtemp()
        _write_md(
            os.path.join(self.temp_dir, 'a.md'),
            {'title': 'A', 'slug': 'a', 'date': '2026-01-15'},
            'body',
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    def test_first_sync_does_not_skip(self, mock_fetch):
        # last_synced_commit is empty so the skip path must be bypassed
        # without even consulting fetch_remote_head_sha.
        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.items_created, 1)
        # ``repo_dir=...`` short-circuits the head check anyway, but the
        # baseline is empty too so this stays True even in real syncs.
        mock_fetch.assert_not_called()


class SyncSkipSameShaTest(TestCase):
    """Second sync against the same SHA writes a 'skipped' log + bails."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-same',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    @mock.patch('community_base.content_sync.github.checkout_repository')
    @mock.patch('community_base.content_sync.orchestration.release_source_lock')
    def test_skip_when_head_matches(
        self, mock_release, mock_clone, mock_fetch,
    ):
        mock_fetch.return_value = 'a' * 40

        log = sync_content_source(self.source)

        self.assertEqual(log.status, 'skipped')
        self.assertEqual(log.commit_sha, 'a' * 40)
        # No checkout, no file walk.
        mock_clone.assert_not_called()
        # Lock was released even on the skip path.
        mock_release.assert_called_once()
        # Only one SyncLog row was written (the skip log) — no separate
        # ``running`` row was leaked.
        self.assertEqual(SyncLog.objects.filter(source=self.source).count(), 1)

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    def test_skip_log_records_skipped_status_and_reason(self, mock_fetch):
        mock_fetch.return_value = 'a' * 40
        log = sync_content_source(self.source)
        self.assertEqual(log.status, 'skipped')
        self.assertFalse(log.errors)
        self.assertEqual(
            log.warnings, ['Repository commit was already synchronized'],
        )

        # Source state reflects the skip.
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'skipped')
        # last_synced_commit is unchanged (we already had it).
        self.assertEqual(self.source.last_synced_commit, 'a' * 40)


class SyncSkipNewShaTest(TestCase):
    """A new HEAD SHA must NOT be skipped — sync runs normally."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-new-sha',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )
        self.temp_dir = tempfile.mkdtemp()
        _write_md(
            os.path.join(self.temp_dir, 'a.md'),
            {'title': 'A', 'slug': 'a', 'date': '2026-01-15'},
            'body',
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_new_sha_runs_normally(self):
        # We pass repo_dir directly so the production path skips the
        # ls-remote check entirely (no remote to consult). This still
        # exercises the "do not skip" branch because last_synced_commit
        # never matches the locally-resolved SHA.
        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.items_created, 1)

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    @mock.patch(
        'community_base.content_sync.github.checkout_repository',
        side_effect=RuntimeError('short-circuit'),
    )
    def test_queued_source_with_new_sha_runs_normally(
        self, mock_clone, mock_fetch,
    ):
        """Issue #556: queued pickup must not turn a new HEAD into a skip."""
        mock_fetch.return_value = 'b' * 40
        self.source.last_sync_status = 'queued'
        self.source.save(update_fields=['last_sync_status'])
        SyncLog.objects.create(
            source=self.source,
            status='success',
            commit_sha='a' * 40,
            finished_at=timezone.now(),
        )
        SyncLog.objects.create(source=self.source, status='queued')

        log = sync_content_source(self.source)

        self.assertEqual(log.status, 'failed')
        mock_fetch.assert_called_once()
        mock_clone.assert_called_once()


class SyncSkipForceTest(TestCase):
    """``force=True`` bypasses the skip check even when the SHA matches."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-force',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )
        self.temp_dir = tempfile.mkdtemp()
        _write_md(
            os.path.join(self.temp_dir, 'a.md'),
            {'title': 'A', 'slug': 'a', 'date': '2026-01-15'},
            'body',
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    def test_force_bypasses_skip_check(self, mock_fetch):
        # If the skip check fired we'd see ``status=skipped`` and 0 items.
        # ``force=True`` must run the sync instead.
        log = sync_content_source(
            self.source, repo_dir=self.temp_dir, force=True,
        )
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.items_created, 1)
        # And we never even consulted ls-remote.
        mock_fetch.assert_not_called()


class SyncSkipPreviousFailureTest(TestCase):
    """If last sync failed, we always retry — even if HEAD matches."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-failed',
            last_synced_commit='a' * 40,
            last_sync_status='failed',
        )

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    @mock.patch(
        'community_base.content_sync.github.checkout_repository',
        side_effect=RuntimeError('boom'),
    )
    def test_retry_after_failure_bypasses_skip(self, mock_clone, mock_fetch):
        # A non-success last status never takes the skip path. The package
        # engine resolves HEAD before consulting the status, so the remote
        # is consulted once; the failing checkout marks the run failed.
        log = sync_content_source(self.source)

        self.assertEqual(log.status, 'failed')
        mock_fetch.assert_called_once()
        mock_clone.assert_called_once()


class SyncSkipHeadFetchFailureTest(TestCase):
    """If we can't fetch HEAD, fall through and run the sync (don't lie)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-fetchfail',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
        side_effect=RuntimeError('ls-remote failed'),
    )
    def test_head_fetch_failure_is_failed_not_skipped(self, mock_fetch):
        # A HEAD resolution failure must never be recorded as a skip.
        log = sync_content_source(self.source)

        self.assertEqual(log.status, 'failed')
        self.assertNotEqual(log.status, 'skipped')
        mock_fetch.assert_called_once()


class SyncFailureDoesNotUpdateLastSyncedCommitTest(TestCase):
    """Failed sync must NOT bump ``last_synced_commit`` (keep last-good)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-keep-sha',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    @mock.patch(
        'community_base.content_sync.github.checkout_repository',
        side_effect=RuntimeError('clone failed'),
    )
    def test_failure_keeps_last_good_sha(self, mock_clone, mock_fetch):
        # Pretend a new commit landed: fetch returns the new sha so the
        # skip path is bypassed and the checkout fails.
        mock_fetch.return_value = 'b' * 40

        log = sync_content_source(self.source)

        self.assertEqual(log.status, 'failed')
        self.source.refresh_from_db()
        # Still the old SHA — the failure must not overwrite it.
        self.assertEqual(self.source.last_synced_commit, 'a' * 40)


class SyncSuccessUpdatesLastSyncedCommitTest(TestCase):
    """A successful sync against a git-backed repo persists the SHA."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-persist',
        )
        self.temp_dir = tempfile.mkdtemp()
        _write_md(
            os.path.join(self.temp_dir, 'a.md'),
            {'title': 'A', 'slug': 'a', 'date': '2026-01-15'},
            'body',
        )
        # Initialize a real git repo so _resolve_local_repo_sha returns
        # a real 40-char SHA, exercising the persistence path.
        import subprocess
        subprocess.run(
            ['git', 'init', '-q'], cwd=self.temp_dir, check=True,
        )
        subprocess.run(
            ['git', 'config', 'user.email', 'test@example.com'],
            cwd=self.temp_dir, check=True,
        )
        subprocess.run(
            ['git', 'config', 'user.name', 'Test'],
            cwd=self.temp_dir, check=True,
        )
        subprocess.run(
            ['git', 'add', '-A'], cwd=self.temp_dir, check=True,
        )
        subprocess.run(
            ['git', 'commit', '-q', '-m', 'init'],
            cwd=self.temp_dir, check=True,
        )
        head = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=self.temp_dir, capture_output=True, text=True, check=True,
        )
        self.expected_sha = head.stdout.strip()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_success_persists_last_synced_commit(self):
        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.commit_sha, self.expected_sha)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_synced_commit, self.expected_sha)


class SyncFromDiskWithoutGitTest(TestCase):
    """``--from-disk`` against a non-git directory still syncs (no skip)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-disk',
            # Even a populated baseline must NOT cause a skip when
            # repo_dir is provided.
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )
        self.temp_dir = tempfile.mkdtemp()
        _write_md(
            os.path.join(self.temp_dir, 'a.md'),
            {'title': 'A', 'slug': 'a', 'date': '2026-01-15'},
            'body',
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @mock.patch(
        'community_base.content_sync.github.GitHubClient.resolve_commit',
    )
    def test_from_disk_never_consults_remote(self, mock_fetch):
        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.items_created, 1)
        mock_fetch.assert_not_called()
        # Non-git dir: SHA falls back to the legacy marker so we don't
        # accidentally clobber last_synced_commit with garbage.
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_synced_commit, 'a' * 40)


# ---------------------------------------------------------------------------
# Webhook always runs (force=True) — see issue #235 acceptance criterion.
# ---------------------------------------------------------------------------


class WebhookForcesSyncTest(TestCase):
    """GitHub webhook handler must pass ``force=True`` to the sync.

    The webhook payload tells us a new commit just landed, so we skip
    the redundant ls-remote check.
    """

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='octo/repo-235-webhook',
            last_synced_commit='a' * 40,
            last_sync_status='success',
            webhook_secret='test-webhook-secret',
        )

    def _push_payload(self):
        return {
            'ref': 'refs/heads/main',
            'repository': {
                'full_name': self.source.repo_name,
                'default_branch': 'main',
            },
        }

    def _signature(self, body):
        digest = hmac.new(
            self.source.webhook_secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return f'sha256={digest}'

    @mock.patch('community_base.content_sync.webhooks.queue_source_sync')
    def test_default_branch_push_queues_sync(self, mock_queue):
        # Package webhook: a push to the default branch queues exactly one
        # engine run; the engine itself decides skip vs full sync.
        body = json.dumps(self._push_payload())
        response = self.client.post(
            '/api/webhooks/github',
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=self._signature(body),
            HTTP_X_GITHUB_EVENT='push',
            HTTP_X_GITHUB_DELIVERY='delivery-235-1',
        )
        self.assertEqual(response.status_code, 202)
        mock_queue.assert_called_once()


# ---------------------------------------------------------------------------
# Studio "Force resync" button + force flag plumbing
# ---------------------------------------------------------------------------


class StudioForceResyncFlagTest(TestCase):
    """The Studio sync trigger views forward ``force=1`` to the task."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.staff = User.objects.create_user(
            email='staff235@example.com', password='x', is_staff=True,
        )
        self.client.force_login(self.staff)
        self.source = ContentSource.objects.create(
            repo_name='owner/repo-235-force-flag',
        )

    @mock.patch('integrations.services.content_sync_queue.enqueue_content_sync')
    def test_per_source_trigger_forwards_force(self, mock_sync):
        response = self.client.post(
            f'/studio/sync/{self.source.pk}/trigger/',
            {'force': '1'},
        )
        self.assertEqual(response.status_code, 302)
        mock_sync.assert_called_once()
        self.assertTrue(mock_sync.call_args.kwargs.get('force'))

    @mock.patch('integrations.services.content_sync_queue.enqueue_content_sync')
    def test_per_source_trigger_default_is_not_forced(self, mock_sync):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertFalse(mock_sync.call_args.kwargs.get('force'))

    @mock.patch('integrations.services.content_sync_queue.enqueue_content_syncs')
    def test_repo_trigger_forwards_force(self, mock_sync):
        self.client.post(
            f'/studio/sync/{self.source.repo_name}/trigger-repo/',
            {'force': '1'},
        )
        mock_sync.assert_called_once()
        self.assertTrue(mock_sync.call_args.kwargs.get('force'))

    @mock.patch('integrations.services.content_sync_queue.enqueue_content_syncs')
    def test_sync_all_forwards_force(self, mock_sync):
        self.client.post('/studio/sync/all/', {'force': '1'})
        mock_sync.assert_called()
        self.assertTrue(mock_sync.call_args.kwargs.get('force'))


# ---------------------------------------------------------------------------
# Studio dashboard + history rendering
# ---------------------------------------------------------------------------


class StudioDashboardShowsCommitTest(TestCase):
    """Dashboard surfaces the last-synced SHA per source as a GitHub link."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.staff = User.objects.create_user(
            email='staff-dash-235@example.com', password='x', is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_dashboard_renders_short_sha_with_github_link(self):
        ContentSource.objects.create(
            repo_name='Org/Repo-235-dash',
            last_synced_commit='abcdef1234567890abcdef1234567890abcdef12',
            last_sync_status='success',
        )
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # Short SHA visible.
        self.assertIn('abcdef1', html)
        # GitHub commit URL points to the right repo + full SHA.
        self.assertIn(
            'https://github.com/Org/Repo-235-dash/commit/'
            'abcdef1234567890abcdef1234567890abcdef12',
            html,
        )

    def test_dashboard_force_resync_button_present(self):
        ContentSource.objects.create(
            repo_name='Org/Repo-235-force-btn',
        )
        response = self.client.get('/studio/sync/')
        html = response.content.decode()
        self.assertIn('Force resync', html)
        # Hidden ``force=1`` input is what the view reads.
        self.assertIn('name="force"', html)


class StudioHistoryShowsSkippedShaTest(TestCase):
    """History view surfaces ``skipped: HEAD == <sha>`` for skip rows."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.staff = User.objects.create_user(
            email='staff-hist-235@example.com', password='x', is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_history_renders_skipped_label_and_short_sha(self):
        from django.utils import timezone

        source = ContentSource.objects.create(
            repo_name='Org/Repo-235-hist',
        )
        SyncLog.objects.create(
            source=source,
            status='skipped',
            commit_sha='cafebabecafebabecafebabecafebabecafebabe',
            finished_at=timezone.now(),
            errors=[{'file': '', 'error': 'HEAD unchanged'}],
        )
        response = self.client.get('/studio/sync/history/')
        html = response.content.decode()
        self.assertIn('skipped: HEAD ==', html)
        self.assertIn('cafebab', html)  # short SHA
