"""Tests for surfacing malformed YAML / frontmatter parse errors (issue #286).

The previous behaviour swallowed parse errors silently inside three helpers:

- ``_parse_yaml_text`` (used by ``detect_content_sources`` for events)
- ``_parse_frontmatter_text`` (used by ``detect_content_sources`` for
  articles/projects)
- ``_build_course_unit_lookup`` (used while syncing courses)

This file pins the new contract: malformed inputs are logged with file
context, classified out of detection rules, and recorded into
``stats['errors']`` (and therefore ``SyncLog.errors``) when stats are
available. ``_parse_yaml_file`` also raises ``ValueError`` for non-mapping
top-level YAML (a list/scalar) instead of returning it silently.

Also covers the relaxed ``ContentSource.unique_together`` that now
includes ``content_path``.
"""

import base64
import os
import shutil
import tempfile
import uuid
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from content.models import Course, Module
from integrations.models import ContentSource
from integrations.services.github import (
    _build_course_unit_lookup,
    _parse_frontmatter_text,
    _parse_yaml_file,
    _parse_yaml_text,
    detect_content_sources,
    sync_content_source,
)

# ============================================================================
# Helpers
# ============================================================================


def _b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


def _mock_response(status_code, json_payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_payload or {}
    response.text = ''
    return response


# ============================================================================
# Scenario: Malformed course.yaml during sync
# ============================================================================


class MalformedCourseYamlTest(TestCase):
    """A malformed ``course.yaml`` is surfaced as an error and does not
    soft-delete a previously-synced course."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/courses',
            content_type='course',
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
            content_type='course',
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
# Scenario: Malformed event YAML is recorded; no Event row is created
# ============================================================================


class MalformedEventYamlTest(TestCase):
    """A malformed event ``.yaml`` produces an error in the SyncLog and
    leaves no Event row behind."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/events',
            content_type='event',
            content_path='events',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.events_dir = os.path.join(self.temp_dir, 'events')
        os.makedirs(self.events_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_malformed_event_yaml_recorded(self):
        from events.models.event import Event

        # Write a malformed event yaml.
        bad_path = os.path.join(self.events_dir, 'broken-event.yaml')
        with open(bad_path, 'w') as f:
            f.write('start_datetime: [[invalid\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # An error pointing at the file is recorded.
        event_errors = [
            e for e in sync_log.errors
            if 'broken-event.yaml' in e.get('file', '')
        ]
        self.assertTrue(
            event_errors,
            f'Expected an error for broken-event.yaml; '
            f'got {sync_log.errors!r}',
        )

        # No Event row was created.
        self.assertFalse(Event.objects.filter(slug='broken-event').exists())


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
            content_type='course',
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
# Scenario: detect_content_sources skips malformed files
# ============================================================================


@override_settings(
    GITHUB_APP_ID='12345',
    GITHUB_APP_PRIVATE_KEY='fake-key',
    GITHUB_APP_INSTALLATION_ID='67890',
)
class DetectContentSourcesMalformedTest(TestCase):
    """Malformed YAML/frontmatter must not bucket files into detection
    rules. They are logged and ignored (issue #286)."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _router(self, repo_meta, tree_entries, file_contents):
        def _r(url, **_kwargs):
            if '/git/trees/' in url:
                return _mock_response(200, {'tree': tree_entries})
            if '/contents/' in url:
                path = url.split('/contents/', 1)[1]
                text = file_contents.get(path)
                if text is None:
                    return _mock_response(404)
                return _mock_response(200, {
                    'encoding': 'base64',
                    'content': _b64(text),
                })
            return _mock_response(200, repo_meta)
        return _r

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_malformed_event_yaml_is_skipped(self, mock_get, _tok):
        repo_meta = {'default_branch': 'main'}
        tree_entries = [
            # Malformed event yaml at events/bad.yaml.
            {'type': 'blob', 'path': 'events/bad.yaml'},
        ]
        # Has start_datetime but the YAML is not parseable.
        file_contents = {
            'events/bad.yaml': 'start_datetime: [[broken\n',
        }
        mock_get.side_effect = self._router(
            repo_meta, tree_entries, file_contents,
        )

        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ) as cm:
            detections = detect_content_sources(
                'org/repo', force_refresh=True,
            )

        # No event detection because the file failed to parse.
        event_dets = [d for d in detections if d['content_type'] == 'event']
        self.assertEqual(event_dets, [])
        # Warning was logged with the parse error.
        joined = '\n'.join(cm.output)
        self.assertIn('Failed to parse YAML', joined)
        self.assertIn('events/bad.yaml', joined)

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_malformed_frontmatter_skipped_for_article_detection(
        self, mock_get, _tok,
    ):
        repo_meta = {'default_branch': 'main'}
        tree_entries = [
            {'type': 'blob', 'path': 'blog/post.md'},
        ]
        # Would be classified as ``article`` (has ``date:``) — but the
        # frontmatter is malformed.
        file_contents = {
            'blog/post.md': '---\ntitle: "x"\ndate: [[broken\n---\nbody\n',
        }
        mock_get.side_effect = self._router(
            repo_meta, tree_entries, file_contents,
        )

        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ) as cm:
            detections = detect_content_sources(
                'org/repo', force_refresh=True,
            )

        # No article detection because frontmatter parse failed.
        article_dets = [
            d for d in detections if d['content_type'] == 'article'
        ]
        self.assertEqual(article_dets, [])
        # Warning was logged.
        joined = '\n'.join(cm.output)
        self.assertIn('Failed to parse frontmatter', joined)
        self.assertIn('blog/post.md', joined)

    @patch('integrations.services.github.generate_github_app_token',
           return_value='tok')
    @patch('integrations.services.github.requests.get')
    def test_well_formed_files_still_detected_alongside_malformed(
        self, mock_get, _tok,
    ):
        repo_meta = {'default_branch': 'main'}
        tree_entries = [
            {'type': 'blob', 'path': 'events/good.yaml'},
            {'type': 'blob', 'path': 'events/bad.yaml'},
            {'type': 'blob', 'path': 'blog/good-post.md'},
            {'type': 'blob', 'path': 'blog/bad-post.md'},
        ]
        file_contents = {
            'events/good.yaml': (
                'start_datetime: "2026-05-01T18:00:00Z"\n'
                'title: "Good event"\n'
            ),
            'events/bad.yaml': 'start_datetime: [[broken\n',
            'blog/good-post.md': '---\ntitle: "ok"\ndate: "2026-01-01"\n---\nx',
            'blog/bad-post.md': '---\ntitle: "x"\ndate: [[broken\n---\nbody',
        }
        mock_get.side_effect = self._router(
            repo_meta, tree_entries, file_contents,
        )

        detections = detect_content_sources(
            'org/repo', force_refresh=True,
        )

        types = {d['content_type'] for d in detections}
        self.assertIn('event', types)
        self.assertIn('article', types)


# ============================================================================
# Scenario: ContentSource unique_together on (repo, type, path)
# ============================================================================


class ContentSourceUniqueTogetherTest(TestCase):
    """The new ``unique_together`` allows two sources to share
    ``(repo_name, content_type)`` as long as ``content_path`` differs."""

    def test_same_repo_and_type_different_paths_allowed(self):
        ContentSource.objects.create(
            repo_name='my-org/content',
            content_type='article',
            content_path='blog',
        )
        # Same repo + type, different path — must succeed.
        ContentSource.objects.create(
            repo_name='my-org/content',
            content_type='article',
            content_path='tutorials',
        )
        self.assertEqual(
            ContentSource.objects.filter(
                repo_name='my-org/content',
                content_type='article',
            ).count(),
            2,
        )

    def test_full_triple_duplicate_rejected(self):
        ContentSource.objects.create(
            repo_name='my-org/content',
            content_type='article',
            content_path='blog',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ContentSource.objects.create(
                    repo_name='my-org/content',
                    content_type='article',
                    content_path='blog',
                )


# ============================================================================
# Direct unit tests for the parse helpers
# ============================================================================


class ParseHelpersUnitTest(TestCase):
    """Cover the ``(data, error)`` tuple shape introduced for issue #286."""

    def test_parse_yaml_text_success(self):
        data, err = _parse_yaml_text('foo: bar\n')
        self.assertEqual(data, {'foo': 'bar'})
        self.assertIsNone(err)

    def test_parse_yaml_text_failure_returns_error(self):
        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ):
            data, err = _parse_yaml_text(
                'foo: [[[broken', filepath='x.yaml',
            )
        self.assertIsNone(data)
        self.assertIsNotNone(err)
        self.assertTrue(err.startswith('Failed to parse x.yaml:'))

    def test_parse_yaml_text_list_returns_none(self):
        # Top-level list is not a mapping -> ``data`` is None, no error.
        data, err = _parse_yaml_text('- a\n- b\n')
        self.assertIsNone(data)
        self.assertIsNone(err)

    def test_parse_yaml_text_empty(self):
        data, err = _parse_yaml_text('')
        self.assertEqual(data, {})
        self.assertIsNone(err)

    def test_parse_frontmatter_text_success(self):
        text = '---\ntitle: ok\n---\nbody\n'
        meta, err = _parse_frontmatter_text(text)
        self.assertEqual(meta, {'title': 'ok'})
        self.assertIsNone(err)

    def test_parse_frontmatter_text_failure_returns_error(self):
        text = '---\ntitle: [[[broken\n---\nbody\n'
        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ):
            meta, err = _parse_frontmatter_text(text, filepath='post.md')
        self.assertEqual(meta, {})
        self.assertIsNotNone(err)
        self.assertTrue(
            err.startswith('Failed to parse frontmatter in post.md:'),
        )

    def test_parse_frontmatter_text_no_frontmatter_no_error(self):
        # Markdown without frontmatter — meta is empty but no parse error.
        meta, err = _parse_frontmatter_text('just body text\n')
        self.assertEqual(meta, {})
        self.assertIsNone(err)


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
