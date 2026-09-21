"""Preserve source ownership and curriculum rows through the package cutover."""

import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_CUTOVER = ('content', '0069_interviewcompany')
POST_CUTOVER = ('content', '0070_curriculum_package_cutover')


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    return MigrationExecutor(connection).loader.project_state([target]).apps


class CurriculumCutoverMigrationTest(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_preserves_course_source_and_curriculum_ids(self):
        old_apps = _migrate(PRE_CUTOVER)
        OldCourse = old_apps.get_model('content', 'Course')
        OldModule = old_apps.get_model('content', 'Module')
        OldUnit = old_apps.get_model('content', 'Unit')
        content_id = uuid.uuid4()
        course = OldCourse.objects.create(
            slug='migration-course', title='Migration course',
            content_id=content_id,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/migration-course/course.yaml',
            source_commit='a' * 40,
        )
        module = OldModule.objects.create(
            course=course, slug='intro', title='Intro', sort_order=1,
        )
        unit = OldUnit.objects.create(
            module=module, slug='hello', title='Hello', sort_order=1,
        )

        new_apps = _migrate(POST_CUTOVER)
        Course = new_apps.get_model('cb_curriculum', 'Course')
        Module = new_apps.get_model('cb_curriculum', 'Module')
        Unit = new_apps.get_model('cb_curriculum', 'Unit')
        CourseExtension = new_apps.get_model('content', 'CourseExtension')

        copied_course = Course.objects.get(pk=course.pk)
        self.assertEqual(copied_course.source_content_id, content_id)
        self.assertEqual(copied_course.source_path, 'courses/migration-course/course.yaml')
        self.assertEqual(
            CourseExtension.objects.get(course_id=course.pk).source_repo,
            'AI-Shipping-Labs/content',
        )
        self.assertEqual(Module.objects.get(pk=module.pk).course_id, course.pk)
        self.assertEqual(Unit.objects.get(pk=unit.pk).module_id, module.pk)
