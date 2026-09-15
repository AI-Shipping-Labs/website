"""Command-boundary tests for ``sync_content`` (issue #1430)."""

import tempfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from community_base.content_sync.models import (
    ContentSource as PackageContentSource,
)
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from integrations.management.commands.sync_content import (
    Command,
    _DiskSnapshot,
)

COMMAND_MODULE = 'integrations.management.commands.sync_content'
WEBHOOK_SECRET = 'webhook-secret-must-not-leak'


def _result(*, created=0, updated=0, unchanged=0, deleted=0, errors=None):
    return SimpleNamespace(
        items_created=created,
        items_updated=updated,
        items_unchanged=unchanged,
        items_deleted=deleted,
        errors=[] if errors is None else errors,
    )


@contextmanager
def _repo_dir():
    with tempfile.TemporaryDirectory(prefix='sync-content-command-') as directory:
        yield Path(directory)


class SyncContentCommandTest(TestCase):
    def _source(self, repo_name):
        # The command walks the package registry (A2.3); the mirror keeps a
        # legacy row alongside but is not what gets dispatched.
        return PackageContentSource.objects.create(
            slug=repo_name.rsplit('/', 1)[-1].lower(),
            repo_name=repo_name,
            webhook_secret=WEBHOOK_SECRET,
        )

    def _run(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        call_command('sync_content', *args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def _run_with_error(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command('sync_content', *args, stdout=stdout, stderr=stderr)
        return caught.exception, stdout.getvalue(), stderr.getvalue()

    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_missing_disk_path_is_rejected_before_any_dispatch(
        self, sync_source,
    ):
        with _repo_dir() as parent:
            missing = parent / 'missing-content-clone'

            error, stdout, stderr = self._run_with_error(
                '--from-disk', str(missing),
            )

        self.assertIn(f'Disk path does not exist: {missing}', str(error))
        self.assertIn('Clone it first: git clone', str(error))
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')
        sync_source.assert_not_called()

    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_empty_registry_is_rejected_with_seed_guidance(self, sync_source):
        error, stdout, stderr = self._run_with_error()

        self.assertIn('No matching content sources', str(error))
        self.assertIn('seed_content_sources', str(error))
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')
        sync_source.assert_not_called()

    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_remote_syncs_every_source_once_in_repo_name_order(self, sync_source):
        source_z = self._source('AI-Shipping-Labs/zeta')
        source_a = self._source('AI-Shipping-Labs/alpha')
        sync_source.side_effect = [
            _result(created=2, updated=1),
            _result(created=3, updated=4),
        ]

        stdout, stderr = self._run()

        self.assertEqual(
            sync_source.call_args_list,
            [call(source_a, repo_dir=None, force=False),
             call(source_z, repo_dir=None, force=False)],
        )
        self.assertIn('Syncing AI-Shipping-Labs/alpha...', stdout)
        self.assertLess(
            stdout.index('Syncing AI-Shipping-Labs/alpha...'),
            stdout.index('Syncing AI-Shipping-Labs/zeta...'),
        )
        self.assertIn('Done. 5 created, 5 updated total.', stdout)
        self.assertEqual(stderr, '')

    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_disk_sync_passes_repo_dir_and_skips_absent_tiers(
        self, sync_source,
    ):
        source_b = self._source('AI-Shipping-Labs/beta')
        source_a = self._source('AI-Shipping-Labs/alpha')
        sync_source.side_effect = [_result(), _result()]

        with _repo_dir() as repo_dir:
            stdout, stderr = self._run('--from-disk', str(repo_dir))

        # --from-disk is a rehearsal sync: the command forces past the
        # enabled gate so secretless sources still sync, and (A2.3) it
        # syncs a symlink-free snapshot copy of the clone, never the
        # clone itself — the package checkout refuses symlinks and local
        # clones carry tooling symlinks.
        passed_calls = sync_source.call_args_list
        passed_dirs = [entry.kwargs['repo_dir'] for entry in passed_calls]
        self.assertEqual(
            [entry.args[0] for entry in passed_calls],
            [source_a, source_b],
        )
        self.assertEqual(passed_dirs, [passed_dirs[0], passed_dirs[0]])
        self.assertNotEqual(passed_dirs[0], str(repo_dir))
        self.assertTrue(passed_dirs[0].endswith('/repo'))
        self.assertEqual(
            [entry.kwargs['force'] for entry in passed_calls],
            [True, True],
        )
        self.assertNotIn('Syncing tiers.yaml...', stdout)
        self.assertEqual(stderr, '')

    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_partial_errors_continue_aggregate_and_raise_command_error(
        self, sync_source,
    ):
        source_a = self._source('AI-Shipping-Labs/alpha')
        source_b = self._source('AI-Shipping-Labs/beta')
        source_c = self._source('AI-Shipping-Labs/gamma')
        sync_source.side_effect = [
            _result(created=2, updated=1),
            RuntimeError('clone unavailable'),
            _result(
                created=3,
                updated=4,
                errors=[{'file': 'broken.md', 'error': 'invalid frontmatter'}],
            ),
        ]

        error, stdout, stderr = self._run_with_error()

        self.assertNotIsInstance(error, SystemExit)
        self.assertEqual(
            sync_source.call_args_list,
            [
                call(source_a, repo_dir=None, force=False),
                call(source_b, repo_dir=None, force=False),
                call(source_c, repo_dir=None, force=False),
            ],
        )
        self.assertIn('Done. 5 created, 5 updated total.', stdout)
        self.assertIn(
            'FAILED [AI-Shipping-Labs/beta]: clone unavailable', stderr,
        )
        self.assertIn(
            'ERROR [AI-Shipping-Labs/gamma]: invalid frontmatter', stderr,
        )
        self.assertIn('completed with errors', str(error).lower())
        self.assertNotIn(WEBHOOK_SECRET, stdout)
        self.assertNotIn(WEBHOOK_SECRET, stderr)
        self.assertNotIn(WEBHOOK_SECRET, str(error))

    @patch('django.core.management.base.connections.close_all')
    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_django_cli_maps_command_error_to_exit_code_one(
        self, sync_source, close_all,
    ):
        source = self._source('AI-Shipping-Labs/content')
        sync_source.return_value = _result(errors=['invalid article metadata'])
        stdout = StringIO()
        stderr = StringIO()
        command = Command(stdout=stdout, stderr=stderr)

        with self.assertRaises(SystemExit) as caught:
            command.run_from_argv(['manage.py', 'sync_content'])

        self.assertEqual(caught.exception.code, 1)
        sync_source.assert_called_once_with(source, repo_dir=None, force=False)
        self.assertIn('Done. 0 created, 0 updated total.', stdout.getvalue())
        self.assertIn(
            'ERROR [AI-Shipping-Labs/content]: invalid article metadata',
            stderr.getvalue(),
        )
        self.assertIn(
            'CommandError: Content sync completed with errors.',
            stderr.getvalue(),
        )
        close_all.assert_called_once_with()

    @patch(f'{COMMAND_MODULE}.run_sync')
    def test_repeated_runs_keep_stable_once_per_source_dispatch(self, sync_source):
        source_a = self._source('AI-Shipping-Labs/alpha')
        source_b = self._source('AI-Shipping-Labs/beta')
        sync_source.side_effect = [
            _result(created=1),
            _result(updated=1),
            _result(created=1),
            _result(updated=1),
        ]

        first_stdout, first_stderr = self._run()
        second_stdout, second_stderr = self._run()

        self.assertEqual(
            sync_source.call_args_list,
            [
                call(source_a, repo_dir=None, force=False),
                call(source_b, repo_dir=None, force=False),
                call(source_a, repo_dir=None, force=False),
                call(source_b, repo_dir=None, force=False),
            ],
        )
        self.assertIn('Done. 1 created, 1 updated total.', first_stdout)
        self.assertIn('Done. 1 created, 1 updated total.', second_stdout)
        self.assertEqual(first_stderr, '')
        self.assertEqual(second_stderr, '')

        # The command-level tiers.yaml handling tests were retired with the
    # legacy engine: tiers is a registered parser family now (covered by
    # integrations.tests.test_tiers_sync).




class DiskSnapshotCopyRaceTest(TestCase):
    """Issue #1643: a transient sqlite sidecar (``-journal``/``-wal``/``-shm``)
    created by a concurrent process can vanish between the ``os.walk``
    listing and ``copy2``. The snapshot must skip that and still copy
    everything else; a vanished regular file must keep failing the run.
    """

    def _snapshot_entries(self, source_dir):
        with _DiskSnapshot(str(source_dir)) as snapshot:
            return {p.name for p in Path(snapshot).rglob('*') if p.is_file()}

    def _vanishing_copy2(self, vanished_suffix):
        from integrations.management.commands import (
            sync_content as command_module,
        )
        real_copy2 = command_module.shutil.copy2

        def copy2(src, dst):
            if str(src).endswith(vanished_suffix):
                raise FileNotFoundError(
                    2, 'No such file or directory', str(src),
                )
            return real_copy2(src, dst)

        return copy2

    def test_snapshot_skips_sqlite_sidecar_that_vanished_mid_copy(self):
        with _repo_dir() as repo:
            (repo / 'article.md').write_text('# hi', encoding='utf-8')
            (repo / 'test_db.sqlite3-journal').write_text('x', encoding='utf-8')
            with patch(
                f'{COMMAND_MODULE}.shutil.copy2',
                side_effect=self._vanishing_copy2('-journal'),
            ):
                entries = self._snapshot_entries(repo)
        self.assertIn('article.md', entries)
        self.assertNotIn('test_db.sqlite3-journal', entries)

    def test_snapshot_still_fails_when_regular_file_vanishes_mid_copy(self):
        with _repo_dir() as repo:
            (repo / 'article.md').write_text('# hi', encoding='utf-8')
            with patch(
                f'{COMMAND_MODULE}.shutil.copy2',
                side_effect=self._vanishing_copy2('article.md'),
            ):
                with self.assertRaises(FileNotFoundError):
                    self._snapshot_entries(repo)
