"""Migration dependencies must survive new migrations landing (issue #1833).

``content/cb_curriculum_migrations/0001_initial.py`` used to depend on
``('events', '__latest__')``. Django resolves ``__latest__`` to whatever the
events leaf is *today*, so adding ``events.0053`` silently made the already
applied ``cb_curriculum.0001`` depend on an unapplied migration, and
``migrate`` raised InconsistentMigrationHistory on every existing database.

The first test pins the rule statically. The second replays the real
situation: every current migration is applied (an up-to-date production
database) and a brand-new migration is appended to one app's leaf. No
already-applied migration may end up depending on the new one.
"""

from pathlib import Path
from unittest import mock

from django.db.migrations import Migration
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_NAME = '9999_probe_new_migration'


def _is_repo_owned(migration):
    module = __import__(migration.__module__, fromlist=['__file__'])
    path = Path(module.__file__).resolve()
    return REPO_ROOT in path.parents and '.venv' not in path.parts


def _disk_loader():
    loader = MigrationLoader(None, load=False, ignore_no_migrations=True)
    loader.load_disk()
    return loader


class MigrationDependencyPinTest(SimpleTestCase):
    def test_repo_migrations_never_depend_on_latest(self):
        offenders = [
            f'{key[0]}.{key[1]} -> {dependency}'
            for key, migration in _disk_loader().disk_migrations.items()
            if _is_repo_owned(migration)
            for dependency in migration.dependencies
            if dependency[1] == '__latest__'
            and dependency[0] != key[0]
        ]
        self.assertEqual(offenders, [])

    def test_new_leaf_migration_keeps_applied_history_consistent(self):
        baseline = MigrationLoader(None, ignore_no_migrations=True)
        applied = set(baseline.graph.nodes)
        repo_apps = sorted({
            key[0]
            for key, migration in baseline.disk_migrations.items()
            if _is_repo_owned(migration)
        })
        self.assertIn('events', repo_apps)

        for app_label in repo_apps:
            with self.subTest(app=app_label):
                (leaf,) = baseline.graph.leaf_nodes(app_label)
                probe = type(
                    'Migration', (Migration,), {'dependencies': [leaf]},
                )(PROBE_NAME, app_label)
                original_load_disk = MigrationLoader.load_disk

                def load_with_probe(loader, probe=probe, app_label=app_label):
                    original_load_disk(loader)
                    loader.disk_migrations[(app_label, PROBE_NAME)] = probe

                with mock.patch.object(
                    MigrationLoader, 'load_disk', load_with_probe,
                ):
                    graph = MigrationLoader(
                        None, ignore_no_migrations=True,
                    ).graph

                probe_key = (app_label, PROBE_NAME)
                dependents = sorted(
                    child.key
                    for child in graph.node_map[probe_key].children
                    if child.key in applied
                )
                self.assertEqual(
                    dependents, [],
                    f'Applied migrations would depend on a new {app_label} '
                    f'migration: {dependents}',
                )
