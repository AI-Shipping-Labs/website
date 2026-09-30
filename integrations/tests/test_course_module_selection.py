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


class CourseIgnoreNormalizationTest(TestCase):
    def setUp(self):
        self.repo = SyncTestRepo(self, prefix='course-ignore-normalization-')
        self.course_dir = str(self.repo.path / 'course')
        self.stats = {
            'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
            'errors': [], 'items_detail': [],
        }

    def _write_course(self, ignore):
        self.repo.write_yaml('course/course.yaml', {
            'title': 'Ignore cases', 'slug': 'ignore-cases',
            'required_level': 0,
            'content_id': str(uuid5(NAMESPACE_URL, 'ignore-cases')),
            'ignore': ignore,
        })

    def _write_module(self, path, ignore):
        self.repo.write_yaml(f'course/{path}/module.yaml', {
            'title': path.rsplit('/', 1)[-1], 'ignore': ignore,
        })

    def _write_unit(self, path):
        identity = str(uuid5(NAMESPACE_URL, path))
        self.repo.write_markdown(f'course/{path}', {
            'title': path, 'content_id': identity,
        }, 'Unit body.')
        return identity

    def _sync(self):
        with checkout_scope(str(self.repo.path)):
            _sync_single_course(
                self.course_dir, self.repo.path,
                SimpleNamespace(repo_name='test/course-ignore-normalization'),
                'deadbeef', self.stats, set(), set(),
            )

    def test_falsey_metadata_keeps_module_and_submodule_units(self):
        self._write_course(None)
        self._write_module('01-parent', False)
        self._write_module('01-parent/01-child', '')
        self._write_module('02-leaf', 0)
        child_id = self._write_unit('01-parent/01-child/keep.md')
        leaf_id = self._write_unit('02-leaf/keep.md')

        with checkout_scope(str(self.repo.path)):
            lookup = _build_course_unit_lookup(self.course_dir)
            paths, identities = _precompute_course_unit_identities(
                self.course_dir, self.repo.path, [],
            )
        self._sync()

        self.assertEqual(self.stats['errors'], [])
        self.assertEqual(lookup['parent']['children']['child']['files'], {'keep.md': 'keep'})
        self.assertEqual(lookup['leaf']['files'], {'keep.md': 'keep'})
        self.assertEqual(identities, {child_id, leaf_id})
        self.assertEqual(paths, set(Unit.objects.values_list('source_path', flat=True)))

    def test_truthy_module_and_submodule_strings_match_characterwise(self):
        self._write_course(None)
        self._write_module('01-leaf', 'x*')
        self._write_module('02-parent', None)
        self._write_module('02-parent/01-child', 'x*')
        self._write_unit('01-leaf/keep.md')
        self._write_unit('02-parent/01-child/keep.md')

        with checkout_scope(str(self.repo.path)):
            lookup = _build_course_unit_lookup(self.course_dir)
            paths, identities = _precompute_course_unit_identities(
                self.course_dir, self.repo.path, [],
            )
        self._sync()

        self.assertEqual(self.stats['errors'], [])
        self.assertEqual(lookup['leaf']['files'], {})
        self.assertEqual(lookup['parent']['children']['child']['files'], {})
        self.assertEqual((paths, identities), (set(), set()))
        self.assertFalse(Unit.objects.exists())

    def test_truthy_course_string_skips_modules_characterwise(self):
        self._write_course('x*')
        self._write_module('01-leaf', None)
        self._write_unit('01-leaf/keep.md')

        self._sync()

        self.assertEqual(self.stats['errors'], [])
        self.assertFalse(Module.objects.exists())
        self.assertFalse(Unit.objects.exists())

    def test_mixed_scalar_lists_keep_each_depths_matching_scope(self):
        self._write_course([7, '02-leaf/course-skip.md', '02-leaf/course-skip.md'])
        self._write_module('7', None)
        self._write_unit('7/keep.md')
        self._write_module('01-parent', None)
        self._write_module('01-parent/01-child', [True, 'sub-skip.md', 'sub-skip.md'])
        self._write_unit('01-parent/01-child/keep.md')
        self._write_unit('01-parent/01-child/sub-skip.md')
        self._write_module('02-leaf', [None, 'module-skip.md', 'module-skip.md'])
        self._write_unit('02-leaf/keep.md')
        self._write_unit('02-leaf/module-skip.md')
        self._write_unit('02-leaf/course-skip.md')

        self._sync()

        self.assertEqual(self.stats['errors'], [])
        self.assertEqual(set(Unit.objects.values_list('source_path', flat=True)), {
            'course/01-parent/01-child/keep.md', 'course/02-leaf/keep.md',
        })
        self.assertFalse(Module.objects.filter(source_path='course/7').exists())

    def test_truthy_non_iterable_module_ignore_records_error(self):
        self._write_course(None)
        self._write_module('01-leaf', 7)
        self._write_unit('01-leaf/keep.md')

        self._sync()

        self.assertEqual(len(self.stats['errors']), 1)
        self.assertIn("'int' object is not iterable", self.stats['errors'][0]['error'])
        self.assertFalse(Module.objects.exists())
        self.assertFalse(Unit.objects.exists())
