"""Tests for issue #235 — skip sync when HEAD SHA matches last successful sync.

Covers the cheap HEAD-SHA short-circuit added to ``sync_content_source``:
before paying the cost of a full clone + file walk we run
``git ls-remote <url> HEAD`` and compare the result against
``ContentSource.last_synced_commit``. If they match (and the previous sync
ended in ``success``), we write a ``skipped`` SyncLog and return.

The repo-level skip differs from issue #225's per-item change detection:
#225 still does the full clone + file walk and only short-circuits the
``update_or_create`` call per file; this issue avoids the clone + walk
entirely. Both compose.
"""

import os
import shutil
import tempfile
import uuid
from unittest import mock

from django.test import TestCase

from integrations.models import ContentSource, SyncLog
from integrations.services.github import (
    fetch_remote_head_sha,
    sync_content_source,
)


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
# fetch_remote_head_sha
# ---------------------------------------------------------------------------


class FetchRemoteHeadShaTest(TestCase):
    """``git ls-remote`` wrapper. Mocked subprocess so no network is needed."""

    @mock.patch('integrations.services.github_sync.repo.subprocess.run')
    def test_returns_sha_for_public_repo(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout='1234567890abcdef1234567890abcdef12345678\tHEAD\n',
            stderr='',
        )
        sha = fetch_remote_head_sha('owner/repo', is_private=False)
        self.assertEqual(sha, '1234567890abcdef1234567890abcdef12345678')
        # No GitHub App token call for public repos — the URL is
        # https://github.com/... rather than https://x-access-token:...
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], 'git')
        self.assertEqual(cmd[1], 'ls-remote')
        self.assertEqual(cmd[3], 'HEAD')
        self.assertEqual(cmd[2], 'https://github.com/owner/repo.git')

    @mock.patch('integrations.services.github_sync.repo.generate_github_app_token')
    @mock.patch('integrations.services.github_sync.repo.subprocess.run')
    def test_uses_app_token_for_private_repos(self, mock_run, mock_token):
        mock_token.return_value = 'ghs_secret'
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout='abcdef0123456789abcdef0123456789abcdef01\tHEAD\n',
            stderr='',
        )
        sha = fetch_remote_head_sha('owner/private-repo', is_private=True)
        self.assertEqual(sha, 'abcdef0123456789abcdef0123456789abcdef01')
        mock_token.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn('x-access-token:ghs_secret', cmd[2])

    @mock.patch('integrations.services.github_sync.repo.subprocess.run')
    def test_returns_none_when_ls_remote_fails(self, mock_run):
        # Network blip / repo not found. Caller falls through to a real sync.
        mock_run.return_value = mock.Mock(
            returncode=128, stdout='', stderr='fatal: repository not found',
        )
        self.assertIsNone(fetch_remote_head_sha('owner/repo'))

    @mock.patch('integrations.services.github_sync.repo.subprocess.run')
    def test_returns_none_when_output_is_garbage(self, mock_run):
        # ls-remote should always emit a 40-char SHA, but be defensive.
        mock_run.return_value = mock.Mock(
            returncode=0, stdout='not-a-sha\tHEAD\n', stderr='',
        )
        self.assertIsNone(fetch_remote_head_sha('owner/repo'))

    @mock.patch('integrations.services.github_sync.repo.generate_github_app_token')
    def test_returns_none_when_app_token_missing(self, mock_token):
        from integrations.services.github import GitHubSyncError
        mock_token.side_effect = GitHubSyncError('no creds')
        self.assertIsNone(
            fetch_remote_head_sha('owner/repo', is_private=True),
        )


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

    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
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

    @mock.patch('integrations.services.github_sync.orchestration.acquire_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.release_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
    @mock.patch('integrations.services.github_sync.orchestration.clone_or_pull_repo')
    def test_skip_when_head_matches(
        self, mock_clone, mock_fetch, mock_release, mock_acquire,
    ):
        mock_acquire.return_value = True
        mock_fetch.return_value = 'a' * 40

        log = sync_content_source(self.source)

        self.assertEqual(log.status, 'skipped')
        self.assertEqual(log.commit_sha, 'a' * 40)
        # No clone, no file walk.
        mock_clone.assert_not_called()
        # Lock was released even on the skip path.
        mock_release.assert_called_once_with(self.source)
        # Only one SyncLog row was written (the skip log) — no separate
        # ``running`` row was leaked.
        self.assertEqual(SyncLog.objects.filter(source=self.source).count(), 1)

    @mock.patch('integrations.services.github_sync.orchestration.acquire_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.release_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
    def test_skip_log_records_skipped_status_and_reason(
        self, mock_fetch, mock_release, mock_acquire,
    ):
        mock_acquire.return_value = True
        mock_fetch.return_value = 'a' * 40
        log = sync_content_source(self.source)
        self.assertEqual(log.status, 'skipped')
        self.assertEqual(len(log.errors), 1)
        self.assertIn('HEAD unchanged', log.errors[0]['error'])

        # Source state reflects the skip.
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'skipped')
        self.assertIn('HEAD unchanged', self.source.last_sync_log)
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

    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
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

    @mock.patch('integrations.services.github_sync.orchestration.acquire_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
    @mock.patch('integrations.services.github_sync.orchestration.clone_or_pull_repo')
    def test_retry_after_failure_bypasses_skip(
        self, mock_clone, mock_fetch, mock_acquire,
    ):
        mock_acquire.return_value = True
        # We don't even consult ls-remote when the previous status wasn't
        # ``success`` — there's no point asking, we already know we want
        # to run.
        mock_clone.side_effect = RuntimeError('boom')  # short-circuit
        with self.assertLogs('integrations.services.github', level='ERROR') as logs:
            sync_content_source(self.source)
        self.assertIn('Sync failed for owner/blog-235-failed', logs.output[0])
        mock_fetch.assert_not_called()


class SyncSkipHeadFetchFailureTest(TestCase):
    """If we can't fetch HEAD, fall through and run the sync (don't lie)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-fetchfail',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )

    @mock.patch('integrations.services.github_sync.orchestration.acquire_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
    @mock.patch('integrations.services.github_sync.orchestration.clone_or_pull_repo')
    def test_runs_sync_when_head_fetch_returns_none(
        self, mock_clone, mock_fetch, mock_acquire,
    ):
        mock_acquire.return_value = True
        mock_fetch.return_value = None  # ls-remote failed
        # We expect clone_or_pull_repo to be invoked even though
        # last_synced_commit is set — the failure to fetch HEAD must NOT
        # cause us to silently mark the sync as skipped.
        mock_clone.side_effect = RuntimeError('short-circuit')
        with self.assertLogs('integrations.services.github', level='ERROR') as logs:
            sync_content_source(self.source)
        self.assertIn('Sync failed for owner/blog-235-fetchfail', logs.output[0])
        mock_clone.assert_called_once()


class SyncFailureDoesNotUpdateLastSyncedCommitTest(TestCase):
    """Failed sync must NOT bump ``last_synced_commit`` (keep last-good)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='owner/blog-235-keep-sha',
            last_synced_commit='a' * 40,
            last_sync_status='success',
        )

    @mock.patch('integrations.services.github_sync.orchestration.acquire_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.release_sync_lock')
    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
    @mock.patch('integrations.services.github_sync.orchestration.clone_or_pull_repo')
    def test_failure_keeps_last_good_sha(
        self, mock_clone, mock_fetch, mock_release, mock_acquire,
    ):
        mock_acquire.return_value = True
        # Pretend a new commit landed; force=False but this works because
        # fetch returns the new sha so the skip path is bypassed and we
        # try to clone... and the clone fails.
        mock_fetch.return_value = 'b' * 40
        mock_clone.side_effect = RuntimeError('clone failed')

        with self.assertLogs('integrations.services.github', level='ERROR') as logs:
            log = sync_content_source(self.source)

        self.assertEqual(log.status, 'failed')
        self.assertIn('Sync failed for owner/blog-235-keep-sha', logs.output[0])
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

    @mock.patch('integrations.services.github_sync.orchestration.fetch_remote_head_sha')
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
        )

    def _push_payload(self):
        return {
            'ref': 'refs/heads/main',
            'repository': {'full_name': self.source.repo_name},
        }

    @mock.patch('integrations.views.github_webhook.sync_content_source')
    def test_webhook_passes_force_true(self, mock_sync):
        # ImportError path: django_q absent, runs sync inline. We patch
        # sync_content_source itself so we can inspect the kwargs.
        with mock.patch.dict(
            'sys.modules', {'django_q.tasks': None},
        ):
            response = self.client.post(
                '/api/webhooks/github',
                data=self._push_payload(),
                content_type='application/json',
                HTTP_X_GITHUB_EVENT='push',
            )
        self.assertEqual(response.status_code, 200)
        mock_sync.assert_called_once()
        kwargs = mock_sync.call_args.kwargs
        self.assertTrue(kwargs.get('force'))


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

    @mock.patch('integrations.services.content_sync_queue.sync_content_source')
    def test_per_source_trigger_forwards_force(self, mock_sync):
        with mock.patch.dict('sys.modules', {'django_q.tasks': None}):
            response = self.client.post(
                f'/studio/sync/{self.source.pk}/trigger/',
                {'force': '1'},
            )
        self.assertEqual(response.status_code, 302)
        mock_sync.assert_called_once()
        self.assertTrue(mock_sync.call_args.kwargs.get('force'))

    @mock.patch('integrations.services.content_sync_queue.sync_content_source')
    def test_per_source_trigger_default_is_not_forced(self, mock_sync):
        with mock.patch.dict('sys.modules', {'django_q.tasks': None}):
            self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertFalse(mock_sync.call_args.kwargs.get('force'))

    @mock.patch('integrations.services.content_sync_queue.sync_content_source')
    def test_repo_trigger_forwards_force(self, mock_sync):
        with mock.patch.dict('sys.modules', {'django_q.tasks': None}):
            self.client.post(
                f'/studio/sync/{self.source.repo_name}/trigger-repo/',
                {'force': '1'},
            )
        mock_sync.assert_called_once()
        self.assertTrue(mock_sync.call_args.kwargs.get('force'))

    @mock.patch('integrations.services.content_sync_queue.sync_content_source')
    def test_sync_all_forwards_force(self, mock_sync):
        with mock.patch.dict('sys.modules', {'django_q.tasks': None}):
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
