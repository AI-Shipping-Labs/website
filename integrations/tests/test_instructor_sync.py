"""Tests for the instructor sync pipeline (issue #308).

Covers:

- Happy path: every yaml in ``instructors/`` becomes a published row.
- Idempotent re-sync: running twice does not create dupes.
- Stale soft-delete: deleting a yaml flips ``status='draft'`` and
  leaves M2M relationships intact.
- Duplicate id within one sync: error logged, second skipped.
- Invalid id slug: error logged, row not created.
- Workshop / Course / Event yaml referencing ``instructors: [a, b]``:
  M2M attached in order, legacy fields mirrored from the FIRST resolved
  instructor.
- Unknown instructor id in yaml: warning, sync continues, legacy fields
  not blanked when no id resolves.
"""

import os
import shutil
import tempfile

from django.test import TestCase

from content.models import (
    Course,
    CourseInstructor,
    Instructor,
    Workshop,
    WorkshopInstructor,
)
from events.models import Event, EventInstructor
from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class _SyncFixture(TestCase):
    """Common helpers for writing a tmp content repo on disk.

    Issue #310: instructors are dispatched by location (a path component
    named ``instructors``). We always write into a synthetic
    ``instructors/`` subdir at the repo root so the walker buckets the
    yaml as instructor files.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='instructor-sync-')
        self.instructors_dir = os.path.join(self.temp_dir, 'instructors')
        os.makedirs(self.instructors_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, text):
        # Allow callers to write to any path; bare ``<id>.yaml`` filenames
        # default to the ``instructors/`` subdir so existing fixtures Just
        # Work. Path-shaped rel_paths (containing a slash) go through
        # untouched.
        if '/' not in rel_path and rel_path.endswith(('.yaml', '.yml')):
            rel_path = os.path.join('instructors', rel_path)
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(text)
        return full

    def _instructor_source(self):
        return ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            is_private=False,
        )


class InstructorSyncHappyPathTest(_SyncFixture):
    """First sync against a fresh repo creates the right count."""

    def test_first_sync_creates_one_instructor_per_yaml(self):
        self._write('alexey-grigorev.yaml', (
            'id: alexey-grigorev\n'
            'name: Alexey Grigorev\n'
            'bio: |\n'
            '  AI/ML engineer.\n'
            'photo_url: https://cdn.example.com/alexey.jpg\n'
            'links:\n'
            '  - {label: GitHub, url: https://github.com/alexeygrigorev}\n'
        ))
        self._write('jane-doe.yaml', (
            'id: jane-doe\n'
            'name: Jane Doe\n'
        ))

        source = self._instructor_source()
        sync_log = sync_content_source(source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.errors, [], f'Errors: {sync_log.errors}')
        published = Instructor.objects.filter(status='published')
        self.assertEqual(published.count(), 2)

        alexey = Instructor.objects.get(instructor_id='alexey-grigorev')
        self.assertEqual(alexey.name, 'Alexey Grigorev')
        self.assertIn('AI/ML engineer.', alexey.bio_html)
        self.assertEqual(
            alexey.photo_url, 'https://cdn.example.com/alexey.jpg',
        )
        self.assertEqual(alexey.links, [
            {'label': 'GitHub', 'url': 'https://github.com/alexeygrigorev'},
        ])
        self.assertEqual(alexey.source_repo, 'AI-Shipping-Labs/content')

    def test_re_sync_is_idempotent(self):
        self._write('a.yaml', 'id: a\nname: A\n')
        self._write('b.yaml', 'id: b\nname: B\n')
        source = self._instructor_source()
        sync_content_source(source, repo_dir=self.temp_dir)
        log2 = sync_content_source(source, repo_dir=self.temp_dir)

        self.assertEqual(log2.items_unchanged, 2)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(Instructor.objects.count(), 2)

    def test_stale_yaml_deletion_soft_deletes_instructor(self):
        self._write('alexey-grigorev.yaml', (
            'id: alexey-grigorev\nname: Alexey Grigorev\n'
        ))
        self._write('jane-doe.yaml', 'id: jane-doe\nname: Jane Doe\n')
        source = self._instructor_source()
        sync_content_source(source, repo_dir=self.temp_dir)

        # Drop one yaml and resync.
        os.remove(os.path.join(self.instructors_dir, 'jane-doe.yaml'))
        sync_content_source(source, repo_dir=self.temp_dir)

        jane = Instructor.objects.get(instructor_id='jane-doe')
        self.assertEqual(jane.status, 'draft')
        # Row still exists — M2M relationships would be preserved.
        self.assertTrue(
            Instructor.objects.filter(instructor_id='jane-doe').exists(),
        )


class InstructorSyncErrorPathTest(_SyncFixture):
    """Edge cases that must not abort the rest of the sync."""

    def test_invalid_id_slug_logs_error_skips_row(self):
        self._write('bad.yaml', 'id: "Not A Slug!"\nname: Bad\n')
        self._write('good.yaml', 'id: good\nname: Good\n')
        source = self._instructor_source()
        sync_log = sync_content_source(source, repo_dir=self.temp_dir)

        # Good one created, bad one rejected with an error.
        self.assertTrue(
            Instructor.objects.filter(instructor_id='good').exists(),
        )
        self.assertFalse(
            Instructor.objects.filter(name='Bad').exists(),
        )
        self.assertTrue(
            any('must match' in e['error'] for e in sync_log.errors),
            f'Expected slug-format error, got: {sync_log.errors}',
        )

    def test_duplicate_id_within_sync_skips_second(self):
        self._write('first.yaml', 'id: dup\nname: First\n')
        self._write('second.yaml', 'id: dup\nname: Second\n')
        source = self._instructor_source()
        sync_log = sync_content_source(source, repo_dir=self.temp_dir)

        # Only one row, with the FIRST file's name (alphabetically
        # ``first.yaml`` is processed before ``second.yaml`` by the
        # ``sorted(os.listdir)`` walk in the sync).
        self.assertEqual(Instructor.objects.filter(instructor_id='dup').count(), 1)
        self.assertEqual(
            Instructor.objects.get(instructor_id='dup').name, 'First',
        )
        self.assertTrue(
            any('Duplicate' in e['error'] for e in sync_log.errors),
            f'Expected duplicate error, got: {sync_log.errors}',
        )


class WorkshopReferencesInstructorsTest(_SyncFixture):
    """Workshop yaml with ``instructors: [a, b]`` resolves both, mirrors first."""

    def setUp(self):
        super().setUp()
        # Pre-create instructors (as if the instructor sync already ran).
        self.alexey = Instructor.objects.create(
            instructor_id='alexey-grigorev', name='Alexey Grigorev',
            bio='AI/ML engineer.',
            status='published',
        )
        self.jane = Instructor.objects.create(
            instructor_id='jane-doe', name='Jane Doe',
            bio='Data scientist.',
            status='published',
        )

    def test_two_instructors_resolve_in_order_and_mirror_first_to_legacy(self):
        # workshops-content shape: a flat YYYY-MM-DD-slug folder.
        folder = '2026-04-21-demo'
        self._write(f'{folder}/workshop.yaml', (
            'content_id: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n'
            'slug: demo\n'
            'title: "Demo"\n'
            'date: 2026-04-21\n'
            'pages_required_level: 10\n'
            'instructors:\n'
            '  - alexey-grigorev\n'
            '  - jane-doe\n'
        ))

        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
            is_private=False,
        )
        sync_log = sync_content_source(source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.errors, [], f'Errors: {sync_log.errors}')

        workshop = Workshop.objects.get(slug='demo')
        # Legacy mirroring — first instructor's name on the string field.
        self.assertEqual(workshop.instructor_name, 'Alexey Grigorev')

        # M2M attached in yaml order with positions 0, 1.
        through_rows = list(
            WorkshopInstructor.objects.filter(workshop=workshop)
            .order_by('position')
        )
        self.assertEqual(len(through_rows), 2)
        self.assertEqual(through_rows[0].instructor_id, self.alexey.pk)
        self.assertEqual(through_rows[0].position, 0)
        self.assertEqual(through_rows[1].instructor_id, self.jane.pk)
        self.assertEqual(through_rows[1].position, 1)

    def test_unknown_id_warning_keeps_sync_running(self):
        folder = '2026-04-21-demo'
        self._write(f'{folder}/workshop.yaml', (
            'content_id: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n'
            'slug: demo\n'
            'title: "Demo"\n'
            'date: 2026-04-21\n'
            'pages_required_level: 10\n'
            'instructors:\n'
            '  - alexey-grigorev\n'
            '  - does-not-exist\n'
        ))

        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
            is_private=False,
        )
        sync_log = sync_content_source(source, repo_dir=self.temp_dir)

        # Workshop still created, only the known id was attached.
        workshop = Workshop.objects.get(slug='demo')
        through = list(
            WorkshopInstructor.objects.filter(workshop=workshop)
        )
        self.assertEqual(len(through), 1)
        self.assertEqual(through[0].instructor_id, self.alexey.pk)
        # Sync did not abort over the missing id — workshop was upserted.
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(workshop.instructor_name, 'Alexey Grigorev')

    def test_all_unknown_ids_keeps_legacy_field_from_yaml(self):
        """When every ``instructors:`` id is unknown, the M2M attach
        helper does not blank the legacy field. The yaml's literal
        ``instructor_name:`` (the legacy string-only path) still wins —
        which is the documented behavior for the transition period.
        """
        folder = '2026-04-21-demo'
        self._write(f'{folder}/workshop.yaml', (
            'content_id: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n'
            'slug: demo\n'
            'title: "Demo"\n'
            'date: 2026-04-21\n'
            'pages_required_level: 10\n'
            # Legacy string-only field still authoritative — and our
            # M2M attach must not blank it just because every id is
            # unknown.
            'instructor_name: "Legacy Name From Yaml"\n'
            'instructors:\n'
            '  - never-heard-of-them\n'
        ))

        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
            is_private=False,
        )
        sync_content_source(source, repo_dir=self.temp_dir)

        ws = Workshop.objects.get(slug='demo')
        # Legacy field intact — the M2M attach helper saw an empty
        # resolved list and left the field alone.
        self.assertEqual(ws.instructor_name, 'Legacy Name From Yaml')
        # And no through-rows were created.
        self.assertEqual(
            WorkshopInstructor.objects.filter(workshop=ws).count(), 0,
        )


class CourseReferencesInstructorsTest(_SyncFixture):
    """Course yaml with ``instructors:`` mirrors first instructor's name+bio."""

    def test_course_attaches_m2m_and_mirrors_legacy(self):
        Instructor.objects.create(
            instructor_id='alexey-grigorev',
            name='Alexey Grigorev',
            bio='AI/ML engineer.',
            status='published',
        )

        course_dir = 'ml-zoomcamp'
        self._write(f'{course_dir}/course.yaml', (
            'content_id: 11111111-1111-1111-1111-111111111111\n'
            'slug: ml-zoomcamp\n'
            'title: "ML Zoomcamp"\n'
            'description: A course.\n'
            'instructors:\n'
            '  - alexey-grigorev\n'
        ))

        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            is_private=False,
        )
        sync_log = sync_content_source(source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.errors, [], f'Errors: {sync_log.errors}')

        course = Course.objects.get(slug='ml-zoomcamp')
        self.assertEqual(course.instructor_name, 'Alexey Grigorev')
        self.assertEqual(course.instructor_bio, 'AI/ML engineer.')
        self.assertEqual(
            CourseInstructor.objects.filter(course=course).count(), 1,
        )


class EventReferencesInstructorsTest(_SyncFixture):
    """Event yaml with ``instructors:`` mirrors first instructor's speaker fields."""

    def test_event_attaches_m2m_and_mirrors_speaker(self):
        Instructor.objects.create(
            instructor_id='alexey-grigorev',
            name='Alexey Grigorev',
            bio='AI/ML engineer.',
            status='published',
        )

        # Write under ``events/`` so the walker classifies as event.
        self._write('events/demo-event.yaml', (
            'content_id: 22222222-2222-2222-2222-222222222222\n'
            'slug: demo-event\n'
            'title: "Demo Event"\n'
            'recording_url: https://example.com/v\n'
            'instructors:\n'
            '  - alexey-grigorev\n'
        ))

        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            is_private=False,
        )
        sync_content_source(source, repo_dir=self.temp_dir)

        event = Event.objects.get(slug='demo-event')
        self.assertEqual(event.speaker_name, 'Alexey Grigorev')
        self.assertEqual(event.speaker_bio, 'AI/ML engineer.')
        self.assertEqual(
            EventInstructor.objects.filter(event=event).count(), 1,
        )
