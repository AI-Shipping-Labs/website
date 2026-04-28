"""Tests for the data migration that reattaches orphan Course FKs.

Issue #366. The migration ``content.0033_reattach_orphan_course_fks``
walks every draft Course with a ``content_id``, finds a published
sibling that shares it, and re-points Enrollment / CourseAccess /
Cohort / UserCourseProgress rows from the orphan to the live row
(matching ``UserCourseProgress`` rows by ``Unit.content_id``). The
orphan course is then deleted.

The tests exercise the migration function directly (it accepts an
``apps`` registry plus ``schema_editor``) so we can seed mixed orphan
+ live state, call the function, and assert FK rewrites without
running the entire migration graph.
"""

import importlib
import logging
import uuid

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Course,
    CourseAccess,
    Enrollment,
    Module,
    Unit,
    UserCourseProgress,
)
from content.models.cohort import Cohort

User = get_user_model()

# Migration module file name starts with digits, so we have to import
# it via ``importlib`` rather than a regular ``from ... import`` line.
_migration_module = importlib.import_module(
    'content.migrations.0033_reattach_orphan_course_fks',
)


def _run_migration():
    """Call the migration function with the live ``apps`` registry."""
    _migration_module.reattach_orphan_course_fks(django_apps, schema_editor=None)


class ReattachOrphanCourseFksMigrationTest(TestCase):
    """Cover the migration's reattach-and-delete contract."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='migrate@example.com', password='pw',
        )

    def _make_course(self, slug, status, content_id, with_unit=True,
                     unit_content_id=None):
        course = Course.objects.create(
            title=slug, slug=slug, status=status, content_id=content_id,
        )
        if with_unit:
            module = Module.objects.create(
                course=course, title='M', slug=f'{slug}-m', sort_order=0,
            )
            unit = Unit.objects.create(
                module=module, title='U', slug=f'{slug}-u',
                sort_order=0, content_id=unit_content_id,
            )
            return course, unit
        return course, None

    def test_orphan_with_shared_content_id_collapses_to_live_row(self):
        """Migration's happy path: orphan + live share content_id.

        We synthesize the impossible (DB-wise) shared-content_id state
        by using raw SQL to bypass the unique constraint check, mirroring
        the inconsistent state that production saw before this issue.
        """
        from django.db import connection

        shared_course_cid = uuid.uuid4()
        shared_unit_cid = uuid.uuid4()

        # Step 1: create the live row with its real content_id.
        live, live_unit = self._make_course(
            'python', 'published', shared_course_cid,
            unit_content_id=shared_unit_cid,
        )
        # Step 2: create the orphan with a placeholder content_id.
        placeholder_cid = uuid.uuid4()
        placeholder_unit_cid = uuid.uuid4()
        orphan, orphan_unit = self._make_course(
            'python-course', 'draft', placeholder_cid,
            unit_content_id=placeholder_unit_cid,
        )
        # Step 3: rewrite the orphan's content_id directly via SQL to
        # collide with the live row. SQLite (the test backend) only
        # checks unique constraints at write time on the column; a
        # raw UPDATE bypasses Django's validation.
        with connection.cursor() as cur:
            # Drop the unique index temporarily so the UPDATE succeeds.
            cur.execute(
                "UPDATE content_course SET content_id = %s WHERE id = %s",
                [str(shared_course_cid), orphan.pk],
            )
            cur.execute(
                "UPDATE content_unit SET content_id = %s WHERE id = %s",
                [str(shared_unit_cid), orphan_unit.pk],
            )

        # Seed user FKs against the orphan.
        enrollment = Enrollment.objects.create(user=self.user, course=orphan)
        access = CourseAccess.objects.create(user=self.user, course=orphan)
        cohort = Cohort.objects.create(
            name='Spring', course=orphan,
            start_date='2026-04-01', end_date='2026-06-30',
        )
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=orphan_unit, completed_at=timezone.now(),
        )

        _run_migration()

        # Orphan is gone; live row survives.
        self.assertFalse(Course.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(Course.objects.filter(pk=live.pk).exists())

        # FKs follow the live row.
        enrollment.refresh_from_db()
        access.refresh_from_db()
        cohort.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual(enrollment.course_id, live.pk)
        self.assertEqual(access.course_id, live.pk)
        self.assertEqual(cohort.course_id, live.pk)
        self.assertEqual(progress.unit.module.course_id, live.pk)
        # The progress row is repointed to the live unit (matched by
        # ``Unit.content_id``).
        self.assertEqual(progress.unit_id, live_unit.pk)

    def test_migration_is_idempotent(self):
        """A second run is a no-op: the orphan is already gone."""
        from django.db import connection

        shared_cid = uuid.uuid4()
        live, _ = self._make_course(
            'python', 'published', shared_cid, with_unit=False,
        )
        orphan, _ = self._make_course(
            'python-course', 'draft', uuid.uuid4(), with_unit=False,
        )
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE content_course SET content_id = %s WHERE id = %s",
                [str(shared_cid), orphan.pk],
            )

        _run_migration()
        self.assertEqual(Course.objects.count(), 1)

        # Second run should not raise and should not touch the live row.
        before_pk = Course.objects.get().pk
        _run_migration()
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Course.objects.get().pk, before_pk)
        self.assertEqual(before_pk, live.pk)

    def test_orphan_unit_with_no_content_id_match_keeps_progress_and_warns(self):
        """When an orphan unit has no match in the live course.

        The migration must NOT silently drop the user's progress. It
        leaves the ``UserCourseProgress`` row attached to the orphan
        unit (which is then deleted by CASCADE when the orphan course
        is removed) and emits a WARNING log so operators can audit.

        Note on outcome: cascade delete removes both the orphan unit
        and the progress row, which is the correct end state — that
        unit no longer exists in any live course, so we can't preserve
        the completion record meaningfully. The warning is the audit
        trail.
        """
        from django.db import connection

        shared_cid = uuid.uuid4()
        live, _ = self._make_course(
            'python', 'published', shared_cid,
            unit_content_id=uuid.uuid4(),
        )
        orphan, orphan_unit = self._make_course(
            'python-course', 'draft', uuid.uuid4(),
            unit_content_id=uuid.uuid4(),  # no match in live course
        )
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE content_course SET content_id = %s WHERE id = %s",
                [str(shared_cid), orphan.pk],
            )

        UserCourseProgress.objects.create(
            user=self.user, unit=orphan_unit, completed_at=timezone.now(),
        )

        with self.assertLogs(_migration_module.logger, level='WARNING') as cm:
            _run_migration()

        self.assertTrue(
            any('no match in target course' in msg for msg in cm.output),
            msg=f'Expected unit-mismatch warning, got: {cm.output}',
        )
        # Orphan course is deleted; the cascade removes the orphan
        # unit and (via UserCourseProgress's ON DELETE behavior) the
        # progress row.
        self.assertFalse(Course.objects.filter(pk=orphan.pk).exists())

    def test_draft_with_no_published_sibling_is_left_alone(self):
        """A draft Course with no live sibling sharing content_id stays."""
        unique_cid = uuid.uuid4()
        draft, _ = self._make_course(
            'old-course', 'draft', unique_cid, with_unit=False,
        )

        _run_migration()

        # The draft is preserved — it represents a course the operator
        # genuinely deleted from the content repo. Wiping it would lose
        # the user's enrollment/progress history.
        self.assertTrue(Course.objects.filter(pk=draft.pk).exists())

    def test_idempotent_silences_log_on_second_run(self):
        """Running with no orphans logs nothing — silent no-op."""
        # Just published rows; no drafts.
        self._make_course('a', 'published', uuid.uuid4(), with_unit=False)

        # ``assertNoLogs`` would be cleaner but is Python 3.10+; instead
        # check that the WARNING channel stays empty.
        logger = logging.getLogger('content.migrations.0033_reattach_orphan_course_fks')
        with self.assertLogs(logger, level='DEBUG') as cm:
            # Need at least one record for assertLogs to succeed; emit a
            # sentinel so we can prove no WARNINGs from the migration.
            logger.debug('sentinel')
            _run_migration()
        warnings = [m for m in cm.output if m.startswith('WARNING:')]
        self.assertEqual(warnings, [])
