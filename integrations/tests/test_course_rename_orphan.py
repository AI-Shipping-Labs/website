"""Tests for issue #366 — sync collapses orphan Course rows on rename.

Covers the sync-pipeline guarantee that motivated this issue:

- Renaming a course (slug changes; ``content_id`` stable) updates the
  same Course row in place, with no duplicate row left behind and no
  orphan ``draft`` row holding the old slug.
- Moving a course between repos (same ``content_id``, different
  ``source_repo``) re-uses the existing row instead of creating a
  duplicate row that ``content_id`` ``unique=True`` would reject anyway.
- The stale-course sweep at the end of ``_dispatch_courses`` reattaches
  Enrollment / CourseAccess / Cohort / UserCourseProgress FKs from any
  draft row whose ``content_id`` matches a live published row, then
  deletes the orphan.
- A brand-new ``content_id`` still creates a fresh Course row (regression
  guard so the matcher does not silently merge unrelated courses).
- After a rename, ``_get_in_progress_courses`` resolves the new slug for
  the dashboard's Continue Learning widget.

The migration ``content/migrations/0033_reattach_orphan_course_fks`` is
exercised by ``test_orphan_reattach_migration.py``.
"""

import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Course,
    CourseAccess,
    Enrollment,
    Unit,
    UserCourseProgress,
)
from content.views.home import _get_in_progress_courses
from integrations.models import ContentSource
from integrations.services.github import sync_content_source

User = get_user_model()


COURSE_CID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
MODULE_CID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
UNIT_CID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'


class CourseRenameOrphanSyncTest(TestCase):
    """Sync-side fixes from issue #366."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            email='learner@example.com', password='pw',
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_root_course(self, slug, content_id=COURSE_CID, title=None):
        """Write a minimal single-course repo at ``self.temp_dir``."""
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write(f'title: "{title or slug}"\n')
            f.write(f'slug: "{slug}"\n')
            f.write('description: "A course."\n')
            f.write('instructor_name: "Alexey"\n')
            f.write('required_level: 0\n')
            f.write(f'content_id: "{content_id}"\n')

        module_dir = os.path.join(self.temp_dir, '01-intro')
        os.makedirs(module_dir, exist_ok=True)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: "Intro"\n')
            f.write(f'content_id: "{MODULE_CID}"\n')
        with open(os.path.join(module_dir, '01-hello.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Hello"\n')
            f.write(f'content_id: "{UNIT_CID}"\n')
            f.write('---\n')
            f.write('Body.\n')

    def test_rename_keeps_existing_course_and_reattaches_progress(self):
        """Slug rename with stable content_id must preserve enrollment+progress.

        First sync creates the course at ``python-course``. The user
        enrolls and completes the only unit. The repo then renames the
        course to ``python`` (same ``content_id``) and we re-sync.

        Acceptance: the existing Course row is updated in place (same pk,
        new slug), the user's Enrollment FK still points at it, and the
        ``UserCourseProgress`` row is still attached to the Unit (which
        is also updated in place by ``content_id`` matching). The
        dashboard helper builds a Continue link against the NEW slug.
        """
        self._write_root_course(slug='python-course')
        sync_content_source(self.source, repo_dir=self.temp_dir)

        course = Course.objects.get(slug='python-course')
        unit = Unit.objects.get(module__course=course)
        Enrollment.objects.create(user=self.user, course=course)
        # Note: leave one unit unfinished so the dashboard treats this as
        # in-progress — completing the only unit would mark it finished.
        # Add a second unit before the rename so we can leave it unfinished.
        # (We rewrite the repo below; here we just assert the starting state.)
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=unit, completed_at=timezone.now(),
        )

        # Rewrite repo with new slug, same content_id.
        # Clean previous module dir first (renames can drop the old dir).
        import shutil as _shutil
        _shutil.rmtree(self.temp_dir)
        os.makedirs(self.temp_dir)
        self._write_root_course(slug='python', title='Python')
        # Add a SECOND unit so the course is not 100% complete (otherwise
        # it does not appear in Continue Learning).
        module_dir = os.path.join(self.temp_dir, '01-intro')
        with open(os.path.join(module_dir, '02-next.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Next"\n')
            f.write('content_id: "dddddddd-dddd-dddd-dddd-dddddddddddd"\n')
            f.write('---\n')
            f.write('Next body.\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Single Course row with the new slug; same primary key.
        self.assertEqual(Course.objects.count(), 1)
        renamed = Course.objects.get()
        self.assertEqual(renamed.pk, course.pk)
        self.assertEqual(renamed.slug, 'python')
        self.assertEqual(renamed.status, 'published')

        # Enrollment FK survived the rename.
        enrollment = Enrollment.objects.get(user=self.user)
        self.assertEqual(enrollment.course_id, renamed.pk)

        # UserCourseProgress is attached to a unit on the renamed course.
        progress.refresh_from_db()
        self.assertEqual(progress.unit.module.course_id, renamed.pk)

        # The dashboard helper builds URLs against the NEW slug.
        in_progress = _get_in_progress_courses(self.user, user_level=999)
        self.assertEqual(len(in_progress), 1)
        item = in_progress[0]
        self.assertEqual(item['course'].slug, 'python')
        self.assertTrue(
            item['next_unit'].get_absolute_url().startswith('/courses/python/'),
            msg=(
                'Dashboard built a stale URL after rename: '
                f'{item["next_unit"].get_absolute_url()}'
            ),
        )

    def test_brand_new_content_id_creates_new_course(self):
        """Regression guard: matcher must not merge unrelated courses.

        Two synced courses with different ``content_id`` values must
        produce two distinct Course rows. Without this guard the matcher
        could regress into matching by slug-only and silently collapse
        unrelated courses.
        """
        self._write_root_course(slug='python-course', content_id=COURSE_CID)
        sync_content_source(self.source, repo_dir=self.temp_dir)
        first = Course.objects.get(slug='python-course')

        # Re-sync with a DIFFERENT content_id and different slug.
        import shutil as _shutil
        _shutil.rmtree(self.temp_dir)
        os.makedirs(self.temp_dir)
        new_cid = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
        # Need fresh module/unit content_ids too — UUIDs are unique.
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Other"\n')
            f.write('slug: "other-course"\n')
            f.write('description: "A different course."\n')
            f.write('required_level: 0\n')
            f.write(f'content_id: "{new_cid}"\n')
        module_dir = os.path.join(self.temp_dir, '01-intro')
        os.makedirs(module_dir, exist_ok=True)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: "Intro"\n')
            f.write('content_id: "ffffffff-ffff-ffff-ffff-ffffffffffff"\n')
        with open(os.path.join(module_dir, '01-hello.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Hello"\n')
            f.write('content_id: "11111111-1111-1111-1111-111111111111"\n')
            f.write('---\n')
            f.write('Body.\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        # The first course soft-deleted to draft (no live sibling shares
        # its content_id), the new course created at its own row.
        first.refresh_from_db()
        self.assertEqual(first.status, 'draft')
        new_course = Course.objects.get(slug='other-course')
        self.assertNotEqual(new_course.pk, first.pk)
        self.assertEqual(str(new_course.content_id), new_cid)

    def test_pre_existing_draft_with_same_content_id_is_resurrected(self):
        """Direct exercise of the matcher's resurrect-on-rename path.

        Setup: a draft Course row exists in the DB (left over from a
        previous sync where the course was removed from the repo). The
        user already has Enrollment + CourseAccess + UserCourseProgress
        rows pointing at this draft. We then re-sync with the SAME
        ``content_id`` but at a NEW slug.

        Expected: the matcher's ``content_id`` lookup hits the draft,
        resurrects it (slug renamed, status flipped to published), and
        every FK survives. No new Course row is created.
        """
        # First sync creates the course; the user enrolls + makes progress.
        self._write_root_course(slug='python-course', title='Python')
        sync_content_source(self.source, repo_dir=self.temp_dir)
        original = Course.objects.get(slug='python-course')
        original_unit = Unit.objects.get(module__course=original)
        enrollment = Enrollment.objects.create(user=self.user, course=original)
        access = CourseAccess.objects.create(user=self.user, course=original)
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=original_unit, completed_at=timezone.now(),
        )

        # Repo is removed; sync soft-deletes the course to 'draft'.
        import shutil as _shutil
        _shutil.rmtree(self.temp_dir)
        os.makedirs(self.temp_dir)
        sync_content_source(self.source, repo_dir=self.temp_dir)
        original.refresh_from_db()
        self.assertEqual(original.status, 'draft')

        # Now the operator re-introduces the course at a new slug, same
        # content_id (e.g. they renamed it after deleting+restoring).
        self._write_root_course(slug='python', title='Python')
        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Single Course row at the new slug; the orphan was reused.
        self.assertEqual(Course.objects.count(), 1)
        live = Course.objects.get()
        self.assertEqual(live.pk, original.pk)
        self.assertEqual(live.slug, 'python')
        self.assertEqual(live.status, 'published')

        # All FKs follow the surviving row.
        enrollment.refresh_from_db()
        access.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual(enrollment.course_id, live.pk)
        self.assertEqual(access.course_id, live.pk)
        self.assertEqual(progress.unit.module.course_id, live.pk)

    def test_cross_repo_move_reuses_existing_course(self):
        """Same content_id in a new ``source_repo`` claims the old row.

        ``content_id`` is ``unique=True`` at the DB level, so a duplicate
        insert would violate the constraint. The matcher must look up
        existing rows by ``content_id`` alone — independent of
        ``source_repo`` — and update in place.
        """
        # Sync via the original repo.
        self._write_root_course(slug='python-course')
        sync_content_source(self.source, repo_dir=self.temp_dir)
        original = Course.objects.get()
        self.assertEqual(original.source_repo, 'AI-Shipping-Labs/python-course')

        # Now sync the same content_id from a DIFFERENT repo (e.g. the
        # course was moved into a monorepo).
        other_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/courses',
        )
        sync_content_source(other_source, repo_dir=self.temp_dir)

        # Still one Course row; source_repo flipped to the new repo.
        self.assertEqual(Course.objects.count(), 1)
        moved = Course.objects.get()
        self.assertEqual(moved.pk, original.pk)
        self.assertEqual(moved.source_repo, 'AI-Shipping-Labs/courses')
        self.assertEqual(moved.status, 'published')
