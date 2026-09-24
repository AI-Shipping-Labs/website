"""End-to-end coverage for the default self-paced cohort backfill."""

import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ('content', '0077_homework_homework_url_field_and_more')
POST_MIGRATION = ('content', '0078_backfill_course_self_paced_cohorts')


class CourseDefaultCohortBackfillMigrationTest(TransactionTestCase):
    reset_sequences = True

    def _migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        return MigrationExecutor(connection).loader.project_state([target]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_creates_self_paced_rows_only_for_courses_missing_all_cohorts(self):
        apps_pre = self._migrate_to(PRE_MIGRATION)
        Course = apps_pre.get_model('cb_curriculum', 'Course')
        Cohort = apps_pre.get_model('content', 'Cohort')

        python = Course.objects.create(title='Python', slug='python-backfill')
        buildcamp = Course.objects.create(title='Buildcamp', slug='buildcamp-backfill')
        Cohort.objects.create(
            course=buildcamp,
            name='Cohort 4',
            external_key='cohort-4',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 11, 22),
            mode='cohort',
        )

        apps_post = self._migrate_to(POST_MIGRATION)
        CohortPost = apps_post.get_model('content', 'Cohort')

        generated = CohortPost.objects.get(course_id=python.pk)
        self.assertEqual(generated.name, 'Self-paced')
        self.assertEqual(generated.mode, 'self_paced')
        self.assertIsNone(generated.start_date)
        self.assertIsNone(generated.end_date)
        self.assertEqual(generated.external_key, '')

        buildcamp_cohorts = CohortPost.objects.filter(course_id=buildcamp.pk)
        self.assertEqual(buildcamp_cohorts.count(), 1)
        self.assertEqual(buildcamp_cohorts.get().name, 'Cohort 4')
