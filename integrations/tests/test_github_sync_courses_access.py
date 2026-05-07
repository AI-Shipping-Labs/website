"""GitHub sync tests for the issue #465 access vocabulary.

Covers ``default_unit_access`` on course.yaml and ``access`` on unit
frontmatter:

- string and integer values map to the right DB level
- bad values surface as a sync error and skip the row
- removing the key on resync clears the column back to NULL
- ``is_preview`` + ``access:`` coexist with an info note
- absent key leaves the column NULL and ``is_preview`` False
- pure-helper coverage of ``_parse_access_value``
"""


from django.test import TestCase

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    LEVEL_REGISTERED,
)
from content.models import Course, Unit
from integrations.services.github_sync.common import GitHubSyncError
from integrations.services.github_sync.dispatchers.courses import (
    _parse_access_value,
)
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class ParseAccessValueTest(TestCase):
    """Pure-helper tests: _parse_access_value resolves YAML to ints."""

    def test_named_values_map_to_levels(self):
        cases = [
            ('open', LEVEL_OPEN),
            ('registered', LEVEL_REGISTERED),
            ('basic', LEVEL_BASIC),
            ('main', LEVEL_MAIN),
            ('premium', LEVEL_PREMIUM),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    _parse_access_value(
                        name, field_name='access', rel_path='x.md',
                    ),
                    expected,
                )

    def test_named_values_are_case_insensitive(self):
        for name in ('Open', 'REGISTERED', 'Basic ', '  MAIN'):
            with self.subTest(name=name):
                self.assertIsInstance(
                    _parse_access_value(
                        name, field_name='access', rel_path='x.md',
                    ),
                    int,
                )

    def test_raw_integers_accepted(self):
        for level in (0, 5, 10, 20, 30):
            with self.subTest(level=level):
                self.assertEqual(
                    _parse_access_value(
                        level, field_name='access', rel_path='x.md',
                    ),
                    level,
                )

    def test_unknown_string_raises_with_field_and_path(self):
        with self.assertRaises(GitHubSyncError) as cm:
            _parse_access_value(
                'pro',
                field_name='default_unit_access',
                rel_path='courses/foo/course.yaml',
            )
        msg = str(cm.exception)
        self.assertIn('pro', msg)
        self.assertIn('default_unit_access', msg)
        self.assertIn('courses/foo/course.yaml', msg)

    def test_invalid_int_raises(self):
        with self.assertRaises(GitHubSyncError):
            _parse_access_value(
                7, field_name='access', rel_path='x.md',
            )

    def test_boolean_rejected(self):
        # ``bool`` is a subclass of ``int`` (True == 1) — must not slip
        # through as level 1.
        with self.assertRaises(GitHubSyncError):
            _parse_access_value(
                True, field_name='access', rel_path='x.md',
            )

    def test_none_not_handled_here(self):
        # The caller treats ``None`` (key absent) as "leave NULL" before
        # calling this helper. Passing None is a programmer error and
        # should raise rather than return something silently.
        with self.assertRaises(GitHubSyncError):
            _parse_access_value(
                None, field_name='access', rel_path='x.md',
            )


class _SyncCourseFixture:
    """Minimal helper that writes a course/module/unit tree."""

    def __init__(self, repo, *, course_data=None, unit_meta=None,
                 unit_body='Hello\n'):
        self.repo = repo
        self.course_data = course_data or {}
        self.unit_meta = unit_meta
        self.unit_body = unit_body

    def write(self):
        course_payload = {
            'title': 'Course 465',
            'slug': 'course-465',
            'description': 'desc',
            'instructor_name': 'I',
            'required_level': 0,
            'content_id': '11111111-1111-1111-1111-111111111111',
            **self.course_data,
        }
        self.repo.write_yaml(
            'course-465/course.yaml', course_payload,
        )
        self.repo.write_yaml(
            'course-465/01-module/module.yaml',
            {'title': 'Module', 'sort_order': 1},
        )
        unit_meta = {
            'title': 'Unit 1',
            'sort_order': 1,
            'content_id': '22222222-2222-2222-2222-222222222222',
        }
        if self.unit_meta is not None:
            unit_meta.update(self.unit_meta)
        self.repo.write_markdown(
            'course-465/01-module/01-unit.md',
            unit_meta, self.unit_body,
        )


class CourseDefaultUnitAccessSyncTest(TestCase):
    """Top-level YAML key ``default_unit_access`` lands in the DB."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/courses-465',
            prefix='access-465-',
        )

    def _sync(self, *, course_data=None, unit_meta=None):
        _SyncCourseFixture(
            self.repo, course_data=course_data, unit_meta=unit_meta,
        ).write()
        return sync_repo(self.source, self.repo)

    def test_named_default_unit_access_registered_writes_5(self):
        self._sync(course_data={'default_unit_access': 'registered'})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(
            course.default_unit_required_level, LEVEL_REGISTERED,
        )

    def test_named_default_unit_access_open(self):
        self._sync(course_data={'default_unit_access': 'open'})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(course.default_unit_required_level, LEVEL_OPEN)

    def test_named_default_unit_access_basic(self):
        self._sync(course_data={'default_unit_access': 'basic'})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(course.default_unit_required_level, LEVEL_BASIC)

    def test_named_default_unit_access_main(self):
        self._sync(course_data={'default_unit_access': 'main'})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(course.default_unit_required_level, LEVEL_MAIN)

    def test_named_default_unit_access_premium(self):
        self._sync(course_data={'default_unit_access': 'premium'})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(
            course.default_unit_required_level, LEVEL_PREMIUM,
        )

    def test_raw_integer_default_unit_access_5(self):
        self._sync(course_data={'default_unit_access': 5})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(
            course.default_unit_required_level, LEVEL_REGISTERED,
        )

    def test_absent_default_unit_access_leaves_null(self):
        self._sync()
        course = Course.objects.get(slug='course-465')
        self.assertIsNone(course.default_unit_required_level)

    def test_garbage_default_unit_access_skips_course_with_error(self):
        log = self._sync(course_data={'default_unit_access': 'pro'})
        # Course must NOT have been created.
        self.assertFalse(Course.objects.filter(slug='course-465').exists())
        # SyncLog must record the error mentioning the file, the field
        # name, and the bad value.
        self.assertEqual(log.status, 'partial')
        errors_text = ' '.join(
            (e.get('error') or '') for e in (log.errors or [])
        )
        self.assertIn('default_unit_access', errors_text)
        self.assertIn('pro', errors_text)
        self.assertIn('course.yaml', errors_text)

    def test_resync_after_removing_clears_to_null(self):
        # First sync sets registered; second sync drops the key.
        self._sync(course_data={'default_unit_access': 'registered'})
        course = Course.objects.get(slug='course-465')
        self.assertEqual(
            course.default_unit_required_level, LEVEL_REGISTERED,
        )

        # Rewrite course.yaml without the key (overwrite the same path).
        # Reuse the same content_id so the upsert finds the existing row.
        self.repo.write_yaml(
            'course-465/course.yaml',
            {
                'title': 'Course 465',
                'slug': 'course-465',
                'description': 'desc',
                'instructor_name': 'I',
                'required_level': 0,
                'content_id': '11111111-1111-1111-1111-111111111111',
            },
        )
        sync_repo(self.source, self.repo)
        course.refresh_from_db()
        self.assertIsNone(course.default_unit_required_level)


class UnitAccessSyncTest(TestCase):
    """Per-unit ``access:`` frontmatter writes Unit.required_level."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/courses-465-units',
            prefix='access-465-units-',
        )

    def _sync(self, *, unit_meta=None, course_data=None):
        _SyncCourseFixture(
            self.repo, course_data=course_data, unit_meta=unit_meta,
        ).write()
        return sync_repo(self.source, self.repo)

    def _unit(self):
        return Unit.objects.get(slug='unit')

    def test_access_open_writes_zero(self):
        self._sync(unit_meta={'access': 'open'})
        self.assertEqual(self._unit().required_level, LEVEL_OPEN)

    def test_access_basic_writes_ten(self):
        self._sync(unit_meta={'access': 'basic'})
        self.assertEqual(self._unit().required_level, LEVEL_BASIC)

    def test_access_registered_writes_five(self):
        self._sync(unit_meta={'access': 'registered'})
        self.assertEqual(self._unit().required_level, LEVEL_REGISTERED)

    def test_no_access_no_preview_leaves_null_and_false(self):
        self._sync()
        unit = self._unit()
        self.assertIsNone(unit.required_level)
        self.assertFalse(unit.is_preview)

    def test_access_open_and_is_preview_both_recorded_with_info_note(self):
        log = self._sync(
            unit_meta={'access': 'open', 'is_preview': True},
        )
        unit = self._unit()
        self.assertEqual(unit.required_level, LEVEL_OPEN)
        # is_preview stays a real DB field — templates branch on it.
        self.assertTrue(unit.is_preview)
        # Sync log records an info-severity note about the redundancy.
        notes = [
            e for e in (log.errors or [])
            if e.get('severity') == 'info'
            and 'access:' in (e.get('error') or '')
            and 'is_preview' in (e.get('error') or '')
        ]
        self.assertTrue(
            notes,
            'Expected an info-severity sync note when access: + is_preview: '
            'are both set.',
        )

    def test_garbage_access_marks_unit_as_error_skipped(self):
        log = self._sync(unit_meta={'access': 'pro'})
        self.assertFalse(Unit.objects.filter(slug='unit').exists())
        errors_text = ' '.join(
            (e.get('error') or '') for e in (log.errors or [])
        )
        self.assertIn('access', errors_text)
        self.assertIn('pro', errors_text)

    def test_resync_after_removing_access_clears_to_null(self):
        # First sync sets the override; second sync drops the key.
        self._sync(unit_meta={'access': 'basic'})
        unit = self._unit()
        self.assertEqual(unit.required_level, LEVEL_BASIC)

        # Rewrite the unit markdown without the access key. The fixture
        # generates a fresh content_id by default; force it to match.
        self.repo.write_markdown(
            'course-465/01-module/01-unit.md',
            {
                'title': 'Unit 1',
                'sort_order': 1,
                'content_id': '22222222-2222-2222-2222-222222222222',
            },
            'Hello\n',
        )
        sync_repo(self.source, self.repo)
        unit.refresh_from_db()
        self.assertIsNone(unit.required_level)


class CourseRequiredLevelStillFromYAMLTest(TestCase):
    """Course.required_level keeps its course-level role after #465.

    Setting ``default_unit_access`` must NOT silently move the
    catalog/course-detail tier around.
    """

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/courses-465-roles',
            prefix='access-465-roles-',
        )

    def test_course_required_level_independent_of_default_unit_access(self):
        _SyncCourseFixture(
            self.repo,
            course_data={
                'required_level': 10,  # LEVEL_BASIC
                'default_unit_access': 'registered',
            },
        ).write()
        sync_repo(self.source, self.repo)
        course = Course.objects.get(slug='course-465')
        self.assertEqual(course.required_level, LEVEL_BASIC)
        self.assertEqual(
            course.default_unit_required_level, LEVEL_REGISTERED,
        )
