from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ("content", "0054_download_private_storage")
POST_MIGRATION = ("content", "0055_workshop_preview_token")


def _migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    return MigrationExecutor(connection).loader.project_state([target]).apps


class WorkshopPreviewMigrationTest(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_workshops_receive_distinct_non_null_tokens(self):
        apps = _migrate_to(PRE_MIGRATION)
        Workshop = apps.get_model("content", "Workshop")
        for index in range(2):
            Workshop.objects.create(
                slug=f"migration-preview-{index}", title=f"Migration {index}",
                date="2026-07-18",
            )

        apps = _migrate_to(POST_MIGRATION)
        Workshop = apps.get_model("content", "Workshop")
        tokens = list(
            Workshop.objects.filter(slug__startswith="migration-preview-")
            .order_by("slug")
            .values_list("preview_token", flat=True)
        )
        self.assertEqual(len(tokens), 2)
        self.assertTrue(all(tokens))
        self.assertEqual(len(set(tokens)), 2)
