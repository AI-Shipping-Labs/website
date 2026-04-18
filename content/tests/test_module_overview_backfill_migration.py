"""Tests for the README-as-Unit -> Module.overview backfill migration.

Issue #222: pre-existing README units (slug='readme', sort_order=-1) need
to be migrated onto their parent module's ``overview`` field, then deleted
so they no longer appear in the lesson list / inflate lesson counts.

These tests use Django's MigrationExecutor so we exercise the data
migration end-to-end against a real schema.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

APP = 'content'
PRE_MIGRATION = ('content', '0026_add_module_overview')
POST_MIGRATION = ('content', '0027_backfill_module_overview')


class BackfillModuleOverviewMigrationTest(TransactionTestCase):
    """End-to-end: seed the legacy state, run the migration, assert the move."""

    def _migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        return MigrationExecutor(connection).loader.project_state(
            [target],
        ).apps

    def test_readme_unit_body_lands_on_module_overview_and_unit_is_deleted(self):
        # Roll back to just before the data migration runs.
        apps_pre = self._migrate_to(PRE_MIGRATION)
        Course = apps_pre.get_model(APP, 'Course')
        Module = apps_pre.get_model(APP, 'Module')
        Unit = apps_pre.get_model(APP, 'Unit')

        course = Course.objects.create(
            title='Python Course', slug='py-mig', status='published',
        )
        module = Module.objects.create(
            course=course, title='Intro', slug='intro-mig', sort_order=1,
        )
        # Legacy README unit — exactly the shape the old sync produced.
        Unit.objects.create(
            module=module, title='Intro', slug='readme', sort_order=-1,
            body='Welcome to this module.\n',
            source_path='py-mig/01-intro/README.md',
        )
        # And a couple of real lesson units that must be left intact.
        Unit.objects.create(
            module=module, title='Why', slug='why', sort_order=1,
            body='Why content.',
        )
        Unit.objects.create(
            module=module, title='Setup', slug='setup', sort_order=2,
            body='Setup content.',
        )

        # Run the data migration.
        apps_post = self._migrate_to(POST_MIGRATION)
        ModulePost = apps_post.get_model(APP, 'Module')
        UnitPost = apps_post.get_model(APP, 'Unit')

        module_post = ModulePost.objects.get(pk=module.pk)
        # Body copied onto Module.overview.
        self.assertIn('Welcome to this module.', module_post.overview)
        # And rendered into overview_html.
        self.assertIn('Welcome to this module.', module_post.overview_html)
        # The README unit is gone.
        self.assertFalse(
            UnitPost.objects.filter(
                module=module_post, slug='readme', sort_order=-1,
            ).exists(),
        )
        # Real lessons are untouched.
        remaining_slugs = set(
            UnitPost.objects
            .filter(module=module_post)
            .values_list('slug', flat=True)
        )
        self.assertEqual(remaining_slugs, {'why', 'setup'})

    def test_modules_without_readme_unit_are_left_alone(self):
        apps_pre = self._migrate_to(PRE_MIGRATION)
        Course = apps_pre.get_model(APP, 'Course')
        Module = apps_pre.get_model(APP, 'Module')
        Unit = apps_pre.get_model(APP, 'Unit')

        course = Course.objects.create(
            title='C', slug='c-no-readme', status='published',
        )
        module = Module.objects.create(
            course=course, title='M', slug='m-no-readme', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Real', slug='real', sort_order=1,
            body='Real body.',
        )

        apps_post = self._migrate_to(POST_MIGRATION)
        ModulePost = apps_post.get_model(APP, 'Module')

        module_post = ModulePost.objects.get(pk=module.pk)
        self.assertEqual(module_post.overview, '')
        self.assertEqual(module_post.overview_html, '')

    def test_authentic_readme_named_unit_with_normal_sort_order_is_kept(self):
        """A real Unit that happens to be slugged ``readme`` (but with a
        normal sort_order) must NOT be deleted by the backfill — only the
        legacy pair (slug='readme' AND sort_order=-1) is migrated.
        """
        apps_pre = self._migrate_to(PRE_MIGRATION)
        Course = apps_pre.get_model(APP, 'Course')
        Module = apps_pre.get_model(APP, 'Module')
        Unit = apps_pre.get_model(APP, 'Unit')

        course = Course.objects.create(
            title='C', slug='c-real-readme', status='published',
        )
        module = Module.objects.create(
            course=course, title='M', slug='m-real-readme', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Readme', slug='readme', sort_order=5,
            body='Real lesson that uses the readme slug.',
        )

        apps_post = self._migrate_to(POST_MIGRATION)
        ModulePost = apps_post.get_model(APP, 'Module')
        UnitPost = apps_post.get_model(APP, 'Unit')

        module_post = ModulePost.objects.get(pk=module.pk)
        # Overview was not populated.
        self.assertEqual(module_post.overview, '')
        # The unit is still here.
        self.assertTrue(
            UnitPost.objects.filter(module=module_post, slug='readme').exists(),
        )

    def tearDown(self):
        # Always leave the DB at the latest migration so other tests are
        # not affected.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        targets = executor.loader.graph.leaf_nodes()
        executor.migrate(targets)
