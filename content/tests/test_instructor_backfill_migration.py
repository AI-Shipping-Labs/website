"""Tests for the Instructor backfill data migration (issue #308).

Drives the migration end-to-end via Django's ``MigrationExecutor`` so it
runs against a real schema. Covers:

- Distinct ``(name, bio)`` tuples across Course/Workshop/Event collapse
  to one Instructor per distinct name.
- Two rows that share a name but differ in bio collapse to the longest
  bio.
- Slug collisions (different names that slugify to the same id) get
  ``-2`` / ``-3`` suffixes.
- Through-table rows are created at ``position=0`` for every parent
  row whose legacy name field is non-empty.
- Reverse migration deletes only the backfilled Instructor rows
  (``source_repo IS NULL``) and the through rows that referenced them.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ('content', '0031_instructor_models')
POST_MIGRATION = ('content', '0032_backfill_instructors')
EVENTS_M2M_MIGRATION = ('events', '0010_event_instructors')


def _migrate_to(*targets):
    """Migrate to the given target nodes and return a project state apps.

    Returns the apps registry from the LAST target (so callers can fetch
    historical models at that point).
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(list(targets))
    return MigrationExecutor(connection).loader.project_state(
        list(targets),
    ).apps


class InstructorBackfillMigrationTest(TransactionTestCase):
    """Backfill migration creates Instructor rows from legacy strings."""

    def tearDown(self):
        # Restore the DB to the latest migration so subsequent tests
        # see the post-backfill schema.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        targets = executor.loader.graph.leaf_nodes()
        executor.migrate(targets)

    def _seed_pre(self):
        """Return historical models at the pre-backfill state."""
        apps_pre = _migrate_to(PRE_MIGRATION, EVENTS_M2M_MIGRATION)
        Course = apps_pre.get_model('content', 'Course')
        Workshop = apps_pre.get_model('content', 'Workshop')
        Event = apps_pre.get_model('events', 'Event')
        return apps_pre, Course, Workshop, Event

    def test_distinct_names_become_one_instructor_each(self):
        _apps_pre, Course, Workshop, Event = self._seed_pre()
        Course.objects.create(
            title='ML Zoomcamp', slug='ml-zoomcamp', status='published',
            instructor_name='Alexey Grigorev',
            instructor_bio='AI/ML engineer.',
        )
        Workshop.objects.create(
            slug='demo-ws', title='Demo', date='2026-04-21',
            instructor_name='Bob Builder',
        )
        Event.objects.create(
            slug='ev-1', title='Event 1',
            start_datetime=datetime(2026, 4, 24, tzinfo=dt_timezone.utc),
            speaker_name='Alexey Grigorev',
            speaker_bio='AI/ML engineer.',
        )

        apps_post = _migrate_to(POST_MIGRATION)
        Instructor = apps_post.get_model('content', 'Instructor')
        instructors = list(Instructor.objects.all().order_by('name'))
        names = [i.name for i in instructors]
        self.assertEqual(names, ['Alexey Grigorev', 'Bob Builder'])

        # All backfilled rows have source_repo NULL, status published.
        for inst in instructors:
            self.assertIsNone(inst.source_repo)
            self.assertEqual(inst.status, 'published')

    def test_through_rows_created_at_position_zero(self):
        _apps_pre, Course, Workshop, Event = self._seed_pre()
        course = Course.objects.create(
            title='C', slug='c-1', status='published',
            instructor_name='Alexey Grigorev',
            instructor_bio='Bio A.',
        )
        workshop = Workshop.objects.create(
            slug='w-1', title='W', date='2026-04-21',
            instructor_name='Alexey Grigorev',
        )
        event = Event.objects.create(
            slug='e-1', title='E',
            start_datetime=datetime(2026, 4, 24, tzinfo=dt_timezone.utc),
            speaker_name='Alexey Grigorev',
            speaker_bio='Bio A.',
        )

        apps_post = _migrate_to(POST_MIGRATION)
        Instructor = apps_post.get_model('content', 'Instructor')
        CI = apps_post.get_model('content', 'CourseInstructor')
        WI = apps_post.get_model('content', 'WorkshopInstructor')
        EI = apps_post.get_model('events', 'EventInstructor')

        inst = Instructor.objects.get(name='Alexey Grigorev')
        ci = CI.objects.get(course_id=course.pk, instructor_id=inst.pk)
        wi = WI.objects.get(workshop_id=workshop.pk, instructor_id=inst.pk)
        ei = EI.objects.get(event_id=event.pk, instructor_id=inst.pk)
        self.assertEqual(ci.position, 0)
        self.assertEqual(wi.position, 0)
        self.assertEqual(ei.position, 0)

    def test_collapses_multiple_bios_to_longest(self):
        _apps_pre, Course, _Workshop, Event = self._seed_pre()
        # Same name, two different bios — must collapse to one Instructor
        # whose bio is the LONGER of the two.
        short_bio = 'Short.'
        long_bio = 'A much, much longer biography paragraph that wins.'
        Course.objects.create(
            title='C1', slug='c-short', status='published',
            instructor_name='Alex Person', instructor_bio=short_bio,
        )
        Event.objects.create(
            slug='e-long', title='E',
            start_datetime=datetime(2026, 4, 24, tzinfo=dt_timezone.utc),
            speaker_name='Alex Person', speaker_bio=long_bio,
        )

        apps_post = _migrate_to(POST_MIGRATION)
        Instructor = apps_post.get_model('content', 'Instructor')
        rows = Instructor.objects.filter(name='Alex Person')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().bio, long_bio)

    def test_slug_collision_gets_numeric_suffix(self):
        _apps_pre, Course, _Workshop, _Event = self._seed_pre()
        # Two distinct names that ``slugify`` would map to the same slug.
        Course.objects.create(
            title='Course A', slug='ca', status='published',
            instructor_name='Alexey Grigorev', instructor_bio='Bio.',
        )
        Course.objects.create(
            title='Course B', slug='cb', status='published',
            # Different unicode form / casing that still slugifies the same.
            instructor_name='alexey grigorev', instructor_bio='Bio.',
        )

        apps_post = _migrate_to(POST_MIGRATION)
        Instructor = apps_post.get_model('content', 'Instructor')
        slugs = sorted(Instructor.objects.values_list('instructor_id', flat=True))
        # Both names should be present, with the second getting a -2 suffix.
        self.assertEqual(len(slugs), 2)
        self.assertIn('alexey-grigorev', slugs)
        self.assertIn('alexey-grigorev-2', slugs)

    def test_empty_legacy_names_skipped(self):
        _apps_pre, Course, _Workshop, _Event = self._seed_pre()
        Course.objects.create(
            title='No Inst', slug='no-inst', status='published',
            instructor_name='', instructor_bio='',
        )

        apps_post = _migrate_to(POST_MIGRATION)
        Instructor = apps_post.get_model('content', 'Instructor')
        self.assertEqual(Instructor.objects.count(), 0)

    def test_reverse_migration_deletes_only_backfilled_rows(self):
        _apps_pre, Course, _Workshop, _Event = self._seed_pre()
        course = Course.objects.create(
            title='C', slug='c-rev', status='published',
            instructor_name='Reverse Tester', instructor_bio='Bio.',
        )

        # Forward.
        apps_post = _migrate_to(POST_MIGRATION)
        Instructor = apps_post.get_model('content', 'Instructor')
        backfilled_pk = Instructor.objects.get(name='Reverse Tester').pk
        # Add a "synced" instructor (source_repo set) so we can verify
        # reverse leaves it alone.
        synced = Instructor.objects.create(
            instructor_id='sync-only', name='Sync Only',
            source_repo='AI-Shipping-Labs/content',
        )
        synced_pk = synced.pk

        # Reverse.
        apps_pre = _migrate_to(PRE_MIGRATION, EVENTS_M2M_MIGRATION)
        InstructorPost = apps_pre.get_model('content', 'Instructor')
        CIPost = apps_pre.get_model('content', 'CourseInstructor')
        # Backfilled gone.
        self.assertFalse(
            InstructorPost.objects.filter(pk=backfilled_pk).exists(),
        )
        # Synced row preserved.
        self.assertTrue(
            InstructorPost.objects.filter(pk=synced_pk).exists(),
        )
        # Through row referencing the backfilled instructor is gone too.
        self.assertFalse(
            CIPost.objects.filter(course_id=course.pk).exists(),
        )
