"""Guards for the migration targets that drive the R1 compatibility matrix.

These live outside ``tests.test_r1_migration_compatibility`` on purpose. That
module is the dedicated serial PostgreSQL job (``postgres_migration``), and
``tests.test_test_runner_sharding`` pins its exact contents. The checks here
need no database and no PostgreSQL, so they belong in the ordinary shards where
they give fast feedback on every push.
"""

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from tests.test_r1_migration_compatibility import (
    ORIGINAL_1E5_DEV_LEAVES,
    ORIGINAL_CB4_MIGRATION_LEAVES,
    POST_R1_CORRECTED_LEAVES,
    PRODUCTION_524153B6_LEAVES,
    PRODUCTION_DC075646_LEAVES,
    R1_CORRECTION_MIGRATIONS,
)


class R1TargetConstantsTest(SimpleTestCase):
    def test_corrected_side_still_contains_the_r1_repair_migrations(self):
        """The corrected side must keep the five #1298 post-R1 repairs.

        It is now derived from the live graph's leaves, so a squash or deletion
        that dropped one of these would silently weaken the matrix instead of
        failing it.
        """

        nodes = set(MigrationLoader(None, ignore_no_migrations=True).graph.nodes)
        self.assertEqual(
            [f"{app}.{name}" for app, name in R1_CORRECTION_MIGRATIONS if (app, name) not in nodes],
            [],
        )

    def test_corrected_side_is_at_or_ahead_of_every_frozen_floor(self):
        """The new image can never be behind an old-image compatibility floor."""

        corrected = dict(POST_R1_CORRECTED_LEAVES)
        for floor_name, floor in (
            ("PRODUCTION_524153B6_LEAVES", PRODUCTION_524153B6_LEAVES),
            ("PRODUCTION_DC075646_LEAVES", PRODUCTION_DC075646_LEAVES),
            ("ORIGINAL_CB4_MIGRATION_LEAVES", ORIGINAL_CB4_MIGRATION_LEAVES),
            ("ORIGINAL_1E5_DEV_LEAVES", ORIGINAL_1E5_DEV_LEAVES),
        ):
            for app, migration in floor:
                with self.subTest(floor=floor_name, app=app):
                    self.assertIn(app, corrected)
                    self.assertGreaterEqual(corrected[app], migration)

    def test_events_leaf_tracks_the_graph_rather_than_a_pinned_literal(self):
        """Regression guard for the #1458 breakage.

        A column added to any model that ``reconcile_r1_expand`` reads used to
        fail the whole matrix because the corrected target still pointed at the
        R1-era ``events`` leaf. The corrected side must be the graph leaf.
        """

        graph_leaves = dict(MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes())
        self.assertEqual(dict(POST_R1_CORRECTED_LEAVES)["events"], graph_leaves["events"])
        self.assertNotEqual(
            dict(POST_R1_CORRECTED_LEAVES)["events"],
            dict(PRODUCTION_DC075646_LEAVES)["events"],
        )
