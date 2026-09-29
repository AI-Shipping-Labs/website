"""Course discovery excludes non-module directories consistently."""

from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from django.test import TestCase

from content.models import Module, Unit
from content.sync_parsers.checkout_view import checkout_scope
from content.sync_parsers.families.courses import (
    _build_course_unit_lookup,
    _precompute_course_unit_identities,
    _sync_single_course,
)
from integrations.tests.sync_fixtures import SyncTestRepo


class CourseModuleSelectionTest(TestCase):
    def setUp(self):
        self.repo = SyncTestRepo(self, prefix='course-module-selection-')
        self.course_dir = str(self.repo.path / 'course')
        self.repo.write_yaml('course/course.yaml', {
            'title': 'Discovery', 'slug': 'discovery', 'required_level': 0,
            'content_id': str(uuid5(NAMESPACE_URL, 'discovery-course')),
            'ignore': ['ignored'],
        })
        for name in ('02-zulu', '01-alpha', '.hidden', 'images', 'ignored'):
            self.repo.write_yaml(f'course/{name}/module.yaml', {'title': name})
            self._write_unit(name)
        self._write_unit('missing-metadata')
        self.repo.write_text('course/not-a-directory', 'not a module')

    def _write_unit(self, dirname):
        self.repo.write_markdown(f'course/{dirname}/lesson.md', {
            'title': 'Lesson', 'content_id': str(uuid5(NAMESPACE_URL, dirname)),
        }, 'Lesson body.')

    def test_lookup_identity_scan_and_sync_select_the_same_modules(self):
        stats = {
            'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
            'errors': [], 'items_detail': [],
        }
        with checkout_scope(str(self.repo.path)):
            lookup = _build_course_unit_lookup(self.course_dir, ['ignored'])
            paths, identities = _precompute_course_unit_identities(
                self.course_dir, self.repo.path, ['ignored'],
            )
            _sync_single_course(
                self.course_dir, self.repo.path,
                SimpleNamespace(repo_name='test/course-module-selection'),
                'deadbeef', stats, set(), set(),
            )
        self.assertEqual(list(lookup), ['alpha', 'zulu'])
        for module in lookup.values():
            self.assertEqual(module['files'], {'lesson.md': 'lesson'})
        self.assertEqual(identities, {
            str(uuid5(NAMESPACE_URL, '01-alpha')), str(uuid5(NAMESPACE_URL, '02-zulu')),
        })
        self.assertEqual(paths, {
            'course/01-alpha/lesson.md', 'course/02-zulu/lesson.md',
        })
        self.assertEqual(stats['errors'], [])
        self.assertEqual(list(Module.objects.order_by('pk').values_list(
            'slug', flat=True,
        )), ['alpha', 'zulu'])
        self.assertEqual(set(Unit.objects.values_list('source_path', flat=True)), paths)
