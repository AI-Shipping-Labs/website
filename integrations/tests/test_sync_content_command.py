"""Command-boundary tests for ``sync_content`` (issue #1430)."""

import tempfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from integrations.management.commands.sync_content import Command
from integrations.models import ContentSource

COMMAND_MODULE = 'integrations.management.commands.sync_content'
WEBHOOK_SECRET = 'webhook-secret-must-not-leak'


def _result(*, created=0, updated=0, errors=None):
    return SimpleNamespace(
        items_created=created,
        items_updated=updated,
        errors=[] if errors is None else errors,
    )


@contextmanager
def _repo_dir():
    with tempfile.TemporaryDirectory(prefix='sync-content-command-') as directory:
        yield Path(directory)


class SyncContentCommandTest(TestCase):
    def _source(self, repo_name):
        return ContentSource.objects.create(
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

        self.assertIn('No content sources configured', str(error))
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

        self.assertEqual(
            sync_source.call_args_list,
            [
                call(source_a, repo_dir=str(repo_dir), force=False),
                call(source_b, repo_dir=str(repo_dir), force=False),
            ],
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


