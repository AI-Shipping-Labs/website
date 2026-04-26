"""Tests for surfacing malformed YAML / frontmatter parse errors (issue #286).

The previous behaviour swallowed parse errors silently. This file pins the
new contract: malformed inputs are logged with file context, classified
out of detection rules, and recorded into ``stats['errors']`` (and
therefore ``SyncLog.errors``) when stats are available.
``_parse_yaml_file`` also raises ``ValueError`` for non-mapping
top-level YAML (a list/scalar) instead of returning it silently.

Issue #310: ``ContentSource`` no longer carries ``content_type`` or
``content_path`` — the walker dispatches per file. Test fixtures are
updated accordingly.
"""

import os
import shutil
import tempfile
import uuid

from django.test import TestCase

from content.models import Course, Module
from integrations.models import ContentSource
from integrations.services.github import (
    _build_course_unit_lookup,
    _parse_yaml_file,
    sync_content_source,
)

# ============================================================================
# Scenario: Malformed course.yaml during sync
# ============================================================================


class MalformedCourseYamlTest(TestCase):
    """A malformed ``course.yaml`` is surfaced as an error and does not
    soft-delete a previously-synced course."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/courses',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.course_dir = os.path.join(self.temp_dir, 'machine-learning-zoomcamp')
        os.makedirs(self.course_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_malformed_course_yaml_recorded_no_soft_delete(self):
        # A previously-synced course exists in the DB.
        existing = Course.objects.create(
            title='Machine Learning Zoomcamp',
            slug='machine-learning-zoomcamp',
            description='Existing description.',
            status='published',
            source_repo='test-org/courses',
            source_path='machine-learning-zoomcamp',
            content_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        )

        # Write a malformed course.yaml — flow sequence never closes.
        course_yaml_path = os.path.join(self.course_dir, 'course.yaml')
        with open(course_yaml_path, 'w') as f:
            f.write('title: [[[invalid\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # The error is recorded against course.yaml.
        course_errors = [
            e for e in sync_log.errors
            if e.get('file', '').endswith('course.yaml')
        ]
        self.assertTrue(
            course_errors,
            f'Expected an error for course.yaml; got {sync_log.errors!r}',
        )

        # The course row is not soft-deleted (status remains published).
        existing.refresh_from_db()
        self.assertEqual(existing.status, 'published')
        self.assertEqual(existing.title, 'Machine Learning Zoomcamp')


# ============================================================================
# Scenario: Malformed module.yaml does not stop other modules from syncing
# ============================================================================


class MalformedModuleYamlTest(TestCase):
    """A malformed ``module.yaml`` records an error and skips that module;
    the course and other modules still sync."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/courses',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def test_one_broken_module_does_not_stop_others(self):
        course_cid = str(uuid.uuid4())
        self._write(
            'python-course/course.yaml',
            (
                'title: "Python Course"\n'
                'slug: "python-course"\n'
                'description: "Learn Python"\n'
                f'content_id: "{course_cid}"\n'
            ),
        )
        # Module 1: valid.
        self._write(
            'python-course/01-fundamentals/module.yaml',
            'title: "Fundamentals"\nsort_order: 1\n',
        )
        unit1_cid = str(uuid.uuid4())
        self._write(
            'python-course/01-fundamentals/01-intro.md',
            (
                '---\n'
                'title: "Intro"\n'
                'sort_order: 1\n'
                f'content_id: "{unit1_cid}"\n'
                '---\n'
                'Body.\n'
            ),
        )
        # Module 2: malformed module.yaml.
        self._write(
            'python-course/02-broken/module.yaml',
            'title: [[[invalid\n',
        )
        # Module 3: valid.
        self._write(
            'python-course/03-advanced/module.yaml',
            'title: "Advanced"\nsort_order: 3\n',
        )
        unit3_cid = str(uuid.uuid4())
        self._write(
            'python-course/03-advanced/01-deep.md',
            (
                '---\n'
                'title: "Deep Dive"\n'
                'sort_order: 1\n'
                f'content_id: "{unit3_cid}"\n'
                '---\n'
                'Body.\n'
            ),
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # Course exists.
        course = Course.objects.get(slug='python-course')

        # Module 1 and Module 3 synced; the broken Module 2 did not.
        modules = list(Module.objects.filter(course=course).order_by('sort_order'))
        slugs = [m.slug for m in modules]
        self.assertIn('fundamentals', slugs)
        self.assertIn('advanced', slugs)
        self.assertNotIn('broken', slugs)

        # An error pointing at the broken module.yaml is recorded.
        broken_errors = [
            e for e in sync_log.errors
            if 'module.yaml' in e.get('file', '')
            and '02-broken' in e.get('file', '')
        ]
        self.assertTrue(
            broken_errors,
            f'Expected an error for 02-broken/module.yaml; '
            f'got {sync_log.errors!r}',
        )


# ============================================================================
# Scenario: Top-level YAML is a list, not a mapping
# ============================================================================


class YamlListNotDictTest(TestCase):
    """``_parse_yaml_file`` raises ``ValueError`` for non-mapping YAML, and
    the error bubbles to the caller's per-file try/except."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_yaml_file_rejects_top_level_list(self):
        path = os.path.join(self.temp_dir, 'list.yaml')
        with open(path, 'w') as f:
            f.write('- a\n- b\n')

        with self.assertRaises(ValueError) as ctx:
            _parse_yaml_file(path)

        message = str(ctx.exception)
        self.assertIn('Invalid YAML', message)
        self.assertIn('expected a mapping', message)
        self.assertIn('got list', message)

    def test_parse_yaml_file_rejects_top_level_scalar(self):
        path = os.path.join(self.temp_dir, 'scalar.yaml')
        with open(path, 'w') as f:
            f.write('justastring\n')

        with self.assertRaises(ValueError) as ctx:
            _parse_yaml_file(path)

        message = str(ctx.exception)
        self.assertIn('expected a mapping', message)
        self.assertIn('got str', message)

    def test_parse_yaml_file_returns_empty_dict_for_blank_file(self):
        path = os.path.join(self.temp_dir, 'blank.yaml')
        with open(path, 'w') as f:
            f.write('')
        self.assertEqual(_parse_yaml_file(path), {})

    def test_parse_yaml_file_returns_dict_for_mapping(self):
        path = os.path.join(self.temp_dir, 'good.yaml')
        with open(path, 'w') as f:
            f.write('title: hello\nsort: 1\n')
        self.assertEqual(_parse_yaml_file(path), {'title': 'hello', 'sort': 1})


class CourseYamlListBubblesAsErrorTest(TestCase):
    """End-to-end: a top-level YAML list in course.yaml surfaces as a
    SyncLog error (the new ``_parse_yaml_file`` raises and the existing
    per-file try/except records it)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/courses',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.course_dir = os.path.join(self.temp_dir, 'list-course')
        os.makedirs(self.course_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_course_yaml_list_records_error(self):
        with open(os.path.join(self.course_dir, 'course.yaml'), 'w') as f:
            f.write('- a\n- b\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        list_errors = [
            e for e in sync_log.errors
            if 'course.yaml' in e.get('file', '')
            and 'expected a mapping' in e.get('error', '')
        ]
        self.assertTrue(
            list_errors,
            f'Expected an "expected a mapping" error for course.yaml; '
            f'got {sync_log.errors!r}',
        )
        # No course was created.
        self.assertFalse(
            Course.objects.filter(source_repo='test-org/courses').exists(),
        )


# ============================================================================
# _build_course_unit_lookup with stats (parse-error surfacing)
# ============================================================================


class BuildLookupSurfacesParseErrorsTest(TestCase):
    """When ``stats`` is passed, parse failures append to ``stats['errors']``
    with a deterministic message format (issue #286). Without ``stats``,
    parse failures are only logged."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.course_dir = os.path.join(self.temp_dir, 'python-course')
        os.makedirs(self.course_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.course_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def test_malformed_module_yaml_appended_to_stats(self):
        self._write('01-broken/module.yaml', 'title: [[[broken\n')

        stats = {
            'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
            'errors': [], 'items_detail': [],
        }

        _build_course_unit_lookup(self.course_dir, stats=stats)

        broken = [
            e for e in stats['errors']
            if 'module.yaml' in e.get('file', '')
        ]
        self.assertEqual(len(broken), 1)
        self.assertTrue(
            broken[0]['error'].startswith('Failed to parse module.yaml:'),
        )

    def test_malformed_unit_md_frontmatter_appended_to_stats(self):
        self._write('01-fundamentals/module.yaml', 'title: "Fundamentals"\n')
        self._write(
            '01-fundamentals/01-intro.md',
            '---\ntitle: "x"\ncontent_id: [[broken\n---\nbody\n',
        )

        stats = {
            'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
            'errors': [], 'items_detail': [],
        }

        _build_course_unit_lookup(self.course_dir, stats=stats)

        broken = [
            e for e in stats['errors']
            if '01-intro.md' in e.get('file', '')
        ]
        self.assertEqual(len(broken), 1)
        self.assertTrue(
            broken[0]['error'].startswith(
                'Failed to parse frontmatter in 01-intro.md:',
            ),
        )

    def test_no_stats_only_logs_warning(self):
        self._write('01-broken/module.yaml', 'title: [[[broken\n')

        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ) as cm:
            # Must not raise; ``stats=None`` means warnings only.
            _build_course_unit_lookup(self.course_dir)
        joined = '\n'.join(cm.output)
        self.assertIn('module.yaml', joined)
