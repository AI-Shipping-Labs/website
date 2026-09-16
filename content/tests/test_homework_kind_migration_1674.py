"""Migration safety coverage for the #1674 homework-kind backfill.

House pattern from
``integrations/tests/test_maven_enrollment_step_migration_1659.py``:
``TransactionTestCase``, ``MigrationExecutor`` with ``migrate_from``/
``migrate_to``, fixture rows created via the OLD historical model, migrate
forward, assert on the NEW historical model's ``kind`` per row and the
migrated-row count, then restore the latest migration state.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag('slow_platform')
class HomeworkKindBackfillMigrationTest(TransactionTestCase):
    migrate_from = [('content', '0063_course_access_mode')]
    migrate_to = [('content', '0064_curriculum_nesting')]

    def test_homework_only_units_relabelled_both_populated_left_alone(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldCourse = old_apps.get_model('content', 'Course')
            OldModule = old_apps.get_model('content', 'Module')
            OldUnit = old_apps.get_model('content', 'Unit')

            course = OldCourse.objects.create(
                title='Migration course', slug='migration-course-1674',
                status='published',
            )
            module = OldModule.objects.create(
                course=course, title='Module', slug='module', sort_order=1,
            )
            homework_only = OldUnit.objects.create(
                module=module, title='Homework only', slug='homework-only',
                sort_order=1, body='', homework='Do this exercise.',
            )
            both_populated = OldUnit.objects.create(
                module=module, title='Both populated', slug='both-populated',
                sort_order=2, body='Lesson text.', homework='Also do this.',
            )
            lesson_only = OldUnit.objects.create(
                module=module, title='Lesson only', slug='lesson-only',
                sort_order=3, body='Lesson text.', homework='',
            )
            neither = OldUnit.objects.create(
                module=module, title='Neither', slug='neither', sort_order=4,
                body='', homework='',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewUnit = new_apps.get_model('content', 'Unit')

            self.assertEqual(
                NewUnit.objects.get(pk=homework_only.pk).kind, 'homework',
            )
            self.assertEqual(
                NewUnit.objects.get(pk=both_populated.pk).kind, 'lesson',
            )
            self.assertEqual(
                NewUnit.objects.get(pk=lesson_only.pk).kind, 'lesson',
            )
            self.assertEqual(
                NewUnit.objects.get(pk=neither.pk).kind, 'lesson',
            )
        finally:
            MigrationExecutor(connection).migrate(latest_targets)

    def test_idempotent_rerun_does_not_change_result(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldCourse = old_apps.get_model('content', 'Course')
            OldModule = old_apps.get_model('content', 'Module')
            OldUnit = old_apps.get_model('content', 'Unit')

            course = OldCourse.objects.create(
                title='Idempotent course', slug='idempotent-course-1674',
                status='published',
            )
            module = OldModule.objects.create(
                course=course, title='Module', slug='module', sort_order=1,
            )
            unit = OldUnit.objects.create(
                module=module, title='HW', slug='hw', sort_order=1,
                body='', homework='Exercise.',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)

            # Reverse then re-forward — the forward predicate is based on
            # field content, not the current kind value, so this is a
            # clean no-op re-run, not a second relabel.
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_from)
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)

            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewUnit = new_apps.get_model('content', 'Unit')
            self.assertEqual(NewUnit.objects.get(pk=unit.pk).kind, 'homework')
        finally:
            MigrationExecutor(connection).migrate(latest_targets)

    def test_reverse_resets_kind_without_touching_content(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldCourse = old_apps.get_model('content', 'Course')
            OldModule = old_apps.get_model('content', 'Module')
            OldUnit = old_apps.get_model('content', 'Unit')

            course = OldCourse.objects.create(
                title='Reverse course', slug='reverse-course-1674',
                status='published',
            )
            module = OldModule.objects.create(
                course=course, title='Module', slug='module', sort_order=1,
            )
            unit = OldUnit.objects.create(
                module=module, title='HW', slug='hw', sort_order=1,
                body='', homework='Exercise text.',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewUnit = new_apps.get_model('content', 'Unit')
            self.assertEqual(NewUnit.objects.get(pk=unit.pk).kind, 'homework')

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_from)
            reverted_apps = executor.loader.project_state(self.migrate_from).apps
            RevertedUnit = reverted_apps.get_model('content', 'Unit')
            reverted = RevertedUnit.objects.get(pk=unit.pk)
            self.assertEqual(reverted.homework, 'Exercise text.')
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
