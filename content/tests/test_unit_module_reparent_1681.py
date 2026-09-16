"""Direct sync-parser tests for cross-module unit reparenting (issue #1681).

Companion to ``test_course_nesting_sync_direct_1674.py`` — same no
-credentials-needed local-directory pattern via ``checkout_scope`` and
``_sync_single_course`` called directly, so a unit moving between modules
(the shape the #1675 buildcamp re-import performs on ~200 units) is
verified without any external dependency.

Covers the two defects fixed by #1681:

1. ``_sync_module_units`` resolving a moved unit's identity course-wide by
   ``content_id`` before falling back to the module-scoped lookups, so a
   cross-module move reparents in place instead of raising
   ``IntegrityError`` on the duplicate ``content_id``.
2. The stale-unit sweep running once per course (after the whole
   module/submodule tree has synced) instead of once per module
   directory, so a unit that already moved earlier in the walk is never
   swept as deleted from its old module later in the same run.
"""

import os
import shutil
import tempfile
import uuid

import yaml
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Module, Unit, UserCourseProgress
from content.sync_parsers.checkout_view import checkout_scope
from content.sync_parsers.families.courses import _sync_single_course

User = get_user_model()


def _fresh_stats():
    return {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }


class _FakeSource:
    repo_name = 'AI-Shipping-Labs/direct-sync-reparent'


class DirectSyncFixtureBase(TestCase):
    """Writes a course tree to a real temp dir and syncs it directly."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='direct-sync-1681-')
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.course_dir = os.path.join(self.temp_dir, 'buildcamp')
        os.makedirs(self.course_dir)

    def _write_yaml(self, rel_path, data):
        full = os.path.join(self.course_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            yaml.safe_dump(data, f)

    def _write_markdown(self, rel_path, frontmatter, body):
        full = os.path.join(self.course_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        frontmatter = dict(frontmatter)
        frontmatter.setdefault('content_id', str(uuid.uuid4()))
        with open(full, 'w') as f:
            f.write('---\n')
            yaml.safe_dump(frontmatter, f)
            f.write('---\n')
            f.write(body)

    def _write_course_yaml(self, **overrides):
        data = {
            'title': 'Buildcamp',
            'slug': 'buildcamp-reparent',
            'required_level': 0,
            'content_id': str(uuid.uuid4()),
        }
        data.update(overrides)
        self._write_yaml('course.yaml', data)

    def _sync(self):
        stats = _fresh_stats()
        with checkout_scope(self.temp_dir):
            _sync_single_course(
                self.course_dir, self.temp_dir, _FakeSource(),
                'deadbeef', stats, set(), set(),
            )
        return stats

    def _remove(self, rel_path):
        os.remove(os.path.join(self.course_dir, rel_path))


class UnitCrossModuleReparentTest(DirectSyncFixtureBase):
    """Acceptance criteria 1-3, 6, 10: cross-module move reparents in
    place, preserving PK and UserCourseProgress; move+rename in one sync;
    two units swapping modules; unchanged re-sync after a move is a
    no-op."""

    def setUp(self):
        super().setUp()
        self._write_course_yaml()
        self.user = User.objects.create_user(
            email='learner@example.com', password='pw12345',
        )

    def test_unit_moving_to_different_module_is_reparented_not_duplicated(self):
        lesson_content_id = str(uuid.uuid4())
        self._write_yaml('01-module-a/module.yaml', {'title': 'Module A'})
        self._write_yaml('02-module-b/module.yaml', {'title': 'Module B'})
        self._write_markdown(
            '01-module-a/01-lesson.md',
            {'title': 'Lesson', 'content_id': lesson_content_id},
            'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        unit = Unit.objects.get(content_id=lesson_content_id)
        original_pk = unit.pk
        module_a = Module.objects.get(
            course__slug='buildcamp-reparent', slug='module-a',
        )
        self.assertEqual(unit.module_id, module_a.pk)

        # Mark the lesson complete before the reorg.
        UserCourseProgress.objects.create(
            user=self.user, unit=unit, completed_at=timezone.now(),
        )

        # Move the file from module A's directory to module B's, keeping
        # the same content_id — the exact shape the #1675 re-import
        # performs.
        self._remove('01-module-a/01-lesson.md')
        self._write_markdown(
            '02-module-b/01-lesson.md',
            {'title': 'Lesson', 'content_id': lesson_content_id},
            'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        # No duplicate row, no crash: exactly one Unit with this
        # content_id, same PK as before the move.
        self.assertEqual(
            Unit.objects.filter(content_id=lesson_content_id).count(), 1,
        )
        moved_unit = Unit.objects.get(content_id=lesson_content_id)
        self.assertEqual(moved_unit.pk, original_pk)

        module_b = Module.objects.get(
            course__slug='buildcamp-reparent', slug='module-b',
        )
        self.assertEqual(moved_unit.module_id, module_b.pk)

        # UserCourseProgress followed the unchanged PK — no explicit
        # repointing needed, and no data loss.
        progress = UserCourseProgress.objects.get(
            user=self.user, unit_id=original_pk,
        )
        self.assertIsNotNone(progress.completed_at)

        # The unit is gone from module A entirely (not a leftover
        # duplicate row sitting stale under the old module).
        self.assertFalse(Unit.objects.filter(module=module_a).exists())

    def test_unit_moving_top_level_module_into_new_submodule(self):
        """The exact #1675 shape: a unit that lived directly under a
        top-level module moves into a brand-new submodule of a
        *different* top-level module in the same sync."""
        lesson_content_id = str(uuid.uuid4())
        self._write_yaml('01-week1/module.yaml', {'title': 'Week 1'})
        self._write_markdown(
            '01-week1/01-lesson.md',
            {'title': 'Intro', 'content_id': lesson_content_id},
            'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        unit = Unit.objects.get(content_id=lesson_content_id)
        original_pk = unit.pk
        UserCourseProgress.objects.create(
            user=self.user, unit=unit, completed_at=timezone.now(),
        )

        # Restructure: week1's flat lesson moves into a new submodule
        # under a different top-level module (week2/topics).
        self._remove('01-week1/01-lesson.md')
        self._write_yaml('02-week2/module.yaml', {'title': 'Week 2'})
        self._write_yaml(
            '02-week2/01-topics/module.yaml', {'title': 'Topics'},
        )
        self._write_markdown(
            '02-week2/01-topics/01-lesson.md',
            {'title': 'Intro', 'content_id': lesson_content_id},
            'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        moved_unit = Unit.objects.get(content_id=lesson_content_id)
        self.assertEqual(moved_unit.pk, original_pk)
        topics = Module.objects.get(
            course__slug='buildcamp-reparent', slug='topics',
        )
        self.assertEqual(moved_unit.module_id, topics.pk)
        self.assertEqual(topics.parent.slug, 'week2')

        progress = UserCourseProgress.objects.get(
            user=self.user, unit_id=original_pk,
        )
        self.assertIsNotNone(progress.completed_at)

    def test_unit_move_and_rename_in_same_sync(self):
        lesson_content_id = str(uuid.uuid4())
        self._write_yaml('01-module-a/module.yaml', {'title': 'Module A'})
        self._write_yaml('02-module-b/module.yaml', {'title': 'Module B'})
        self._write_markdown(
            '01-module-a/01-old-name.md',
            {'title': 'Old title', 'content_id': lesson_content_id},
            'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        original_pk = Unit.objects.get(content_id=lesson_content_id).pk

        self._remove('01-module-a/01-old-name.md')
        self._write_markdown(
            '02-module-b/01-new-name.md',
            {'title': 'New title', 'content_id': lesson_content_id},
            'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        unit = Unit.objects.get(content_id=lesson_content_id)
        self.assertEqual(unit.pk, original_pk)
        self.assertEqual(unit.slug, 'new-name')
        self.assertEqual(unit.title, 'New title')
        module_b = Module.objects.get(
            course__slug='buildcamp-reparent', slug='module-b',
        )
        self.assertEqual(unit.module_id, module_b.pk)

    def test_two_units_swap_modules_in_same_sync(self):
        content_id_1 = str(uuid.uuid4())
        content_id_2 = str(uuid.uuid4())
        self._write_yaml('01-module-a/module.yaml', {'title': 'Module A'})
        self._write_yaml('02-module-b/module.yaml', {'title': 'Module B'})
        self._write_markdown(
            '01-module-a/01-lesson-one.md',
            {'title': 'Lesson one', 'content_id': content_id_1}, 'Body 1.\n',
        )
        self._write_markdown(
            '02-module-b/01-lesson-two.md',
            {'title': 'Lesson two', 'content_id': content_id_2}, 'Body 2.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        pk1 = Unit.objects.get(content_id=content_id_1).pk
        pk2 = Unit.objects.get(content_id=content_id_2).pk

        # Swap: lesson one moves to module B, lesson two moves to module A.
        self._remove('01-module-a/01-lesson-one.md')
        self._remove('02-module-b/01-lesson-two.md')
        self._write_markdown(
            '02-module-b/01-lesson-one.md',
            {'title': 'Lesson one', 'content_id': content_id_1}, 'Body 1.\n',
        )
        self._write_markdown(
            '01-module-a/01-lesson-two.md',
            {'title': 'Lesson two', 'content_id': content_id_2}, 'Body 2.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        module_a = Module.objects.get(
            course__slug='buildcamp-reparent', slug='module-a',
        )
        module_b = Module.objects.get(
            course__slug='buildcamp-reparent', slug='module-b',
        )
        unit1 = Unit.objects.get(content_id=content_id_1)
        unit2 = Unit.objects.get(content_id=content_id_2)
        self.assertEqual(unit1.pk, pk1)
        self.assertEqual(unit2.pk, pk2)
        self.assertEqual(unit1.module_id, module_b.pk)
        self.assertEqual(unit2.module_id, module_a.pk)
        # Neither was deleted and re-created.
        self.assertEqual(Unit.objects.filter(content_id=content_id_1).count(), 1)
        self.assertEqual(Unit.objects.filter(content_id=content_id_2).count(), 1)

    def test_unchanged_resync_after_move_is_a_no_op(self):
        lesson_content_id = str(uuid.uuid4())
        self._write_yaml('01-module-a/module.yaml', {'title': 'Module A'})
        self._write_yaml('02-module-b/module.yaml', {'title': 'Module B'})
        self._write_markdown(
            '01-module-a/01-lesson.md',
            {'title': 'Lesson', 'content_id': lesson_content_id}, 'Body.\n',
        )
        self._sync()
        self._remove('01-module-a/01-lesson.md')
        self._write_markdown(
            '02-module-b/01-lesson.md',
            {'title': 'Lesson', 'content_id': lesson_content_id}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        self.assertEqual(stats['created'], 0)
        self.assertGreaterEqual(stats['updated'], 1)

        # Re-run against the identical, already-moved tree: no-op.
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        self.assertEqual(stats['created'], 0)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(stats['deleted'], 0)
        unit = Unit.objects.get(content_id=lesson_content_id)
        module_b = Module.objects.get(
            course__slug='buildcamp-reparent', slug='module-b',
        )
        self.assertEqual(unit.module_id, module_b.pk)


class ModuleReparentDoesNotDropUnitsTest(DirectSyncFixtureBase):
    """Acceptance criterion 7: a module moving to a different parent
    (its directory relocates in the tree) must not cause any of its
    previously-existing units to be deleted, even though the module row
    itself gets a new PK (Module has no content_id — see issue scope)."""

    def setUp(self):
        super().setUp()
        self._write_course_yaml(slug='buildcamp-module-move')

    def test_module_promoted_from_submodule_to_top_level_keeps_its_units(self):
        content_id = str(uuid.uuid4())
        self._write_yaml('01-parent/module.yaml', {'title': 'Parent'})
        self._write_yaml(
            '01-parent/01-child/module.yaml', {'title': 'Child'},
        )
        self._write_markdown(
            '01-parent/01-child/01-lesson.md',
            {'title': 'Lesson', 'content_id': content_id}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        unit_pk = Unit.objects.get(content_id=content_id).pk

        # Promote the child submodule to a top-level module (its
        # directory relocates out from under the parent).
        self._remove('01-parent/01-child/module.yaml')
        self._remove('01-parent/01-child/01-lesson.md')
        try:
            os.rmdir(os.path.join(self.course_dir, '01-parent/01-child'))
        except OSError:
            pass
        self._write_yaml('02-child/module.yaml', {'title': 'Child'})
        self._write_markdown(
            '02-child/01-lesson.md',
            {'title': 'Lesson', 'content_id': content_id}, 'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        # The unit survived the module move — same PK, reparented onto
        # whichever Module row now represents "child" (new top-level
        # row, since Module has no content_id to preserve its identity
        # across a parent change — see issue #1681 scope decision).
        unit = Unit.objects.get(content_id=content_id)
        self.assertEqual(unit.pk, unit_pk)
        new_child = Module.objects.get(
            course__slug='buildcamp-module-move', slug='child',
        )
        self.assertIsNone(new_child.parent_id)
        self.assertEqual(unit.module_id, new_child.pk)


class ModuleSlugCollisionTest(DirectSyncFixtureBase):
    """Acceptance criterion 8: two modules landing on the same slug
    produce a named stats['errors'] entry, not a raw IntegrityError, and
    the rest of the course still syncs."""

    def test_two_top_level_modules_same_slug_produce_named_error(self):
        self._write_course_yaml(slug='buildcamp-module-collision')
        # Two directories whose derived slugs both resolve to "topics".
        self._write_yaml('01-topics/module.yaml', {'title': 'Topics one'})
        self._write_markdown(
            '01-topics/01-lesson.md', {'title': 'Lesson one'}, 'Body.\n',
        )
        self._write_yaml(
            '02-topics-dup/module.yaml',
            {'title': 'Topics two', 'slug': 'topics'},
        )
        self._write_markdown(
            '02-topics-dup/01-lesson.md', {'title': 'Lesson two'}, 'Body.\n',
        )
        # A well-formed sibling module must still sync normally.
        self._write_yaml('03-plain/module.yaml', {'title': 'Plain'})
        self._write_markdown(
            '03-plain/01-lesson.md', {'title': 'Plain lesson'}, 'Body.\n',
        )

        stats = self._sync()

        self.assertTrue(any('IntegrityError' not in e.get('error', '') for e in stats['errors']))
        error_text = ' '.join(e.get('error', '') for e in stats['errors'])
        self.assertNotIn('IntegrityError', error_text)
        self.assertIn('01-topics', error_text)
        self.assertIn('02-topics-dup', error_text)
        self.assertIn('topics', error_text)

        # Exactly one module row exists for the "topics" slug — the
        # first file synced normally, the second was rejected and never
        # created or merged into the first.
        self.assertEqual(
            Module.objects.filter(
                course__slug='buildcamp-module-collision', slug='topics',
            ).count(),
            1,
        )
        topics = Module.objects.get(
            course__slug='buildcamp-module-collision', slug='topics',
        )
        self.assertEqual(topics.source_path, os.path.relpath(
            os.path.join(self.course_dir, '01-topics'), self.temp_dir,
        ))
        self.assertTrue(
            Unit.objects.filter(module=topics, title='Lesson one').exists(),
        )
        self.assertFalse(
            Unit.objects.filter(title='Lesson two').exists(),
        )

        # The rest of the course synced normally.
        plain = Module.objects.get(
            course__slug='buildcamp-module-collision', slug='plain',
        )
        self.assertTrue(
            Unit.objects.filter(module=plain, title='Plain lesson').exists(),
        )


class DuplicateUnitContentIdTest(DirectSyncFixtureBase):
    """Acceptance criterion 9: two different files claiming the same
    content_id in one sync run produce a named error, and neither reaches
    the model layer as a raw IntegrityError."""

    def test_two_files_same_content_id_produce_named_error_and_skip_second(self):
        self._write_course_yaml(slug='buildcamp-dup-content-id')
        shared_content_id = str(uuid.uuid4())
        self._write_yaml('01-module-a/module.yaml', {'title': 'Module A'})
        self._write_yaml('02-module-b/module.yaml', {'title': 'Module B'})
        self._write_markdown(
            '01-module-a/01-first.md',
            {'title': 'First copy', 'content_id': shared_content_id},
            'Body.\n',
        )
        self._write_markdown(
            '02-module-b/01-second.md',
            {'title': 'Second copy', 'content_id': shared_content_id},
            'Body.\n',
        )

        stats = self._sync()

        error_text = ' '.join(e.get('error', '') for e in stats['errors'])
        self.assertNotIn('IntegrityError', error_text)
        self.assertIn(shared_content_id, error_text)
        self.assertIn('01-module-a', error_text)
        self.assertIn('02-module-b', error_text)

        # Only one Unit row exists for this content_id — the first file
        # synced normally, the second was skipped.
        self.assertEqual(
            Unit.objects.filter(content_id=shared_content_id).count(), 1,
        )
        unit = Unit.objects.get(content_id=shared_content_id)
        self.assertEqual(unit.title, 'First copy')
