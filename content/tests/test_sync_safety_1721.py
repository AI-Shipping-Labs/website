"""Direct sync-parser tests for the sync-safety fix (issue #1721).

Companion to ``test_course_nesting_sync_direct_1674.py`` and
``test_unit_module_reparent_1681.py`` — same no-credentials-needed
local-directory pattern via ``checkout_scope`` and ``_sync_single_course``
called directly.

Covers the two bugs found by the #1675 buildcamp restructure dry run:

Bug A (the deadlock): a module that keeps its slug across a restructure
while gaining submodules always failed ``Module.clean()``'s "parent
already has direct units" check, because nothing had reparented its old
direct units onto its own new submodules yet at validation time.

Bug B (the deletion, the one that actually destroyed content): the
course-end fallback stale-unit/stale-module sweeps reasoned from the
REAL, accumulated walk state. An aborted walk (Bug A firing, or any other
per-module/unit error) meant some unit files were never read, so their
identities never entered that state — and the sweeps, working from an
incomplete picture, deleted live content that was never actually gone.
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
    repo_name = 'AI-Shipping-Labs/direct-sync-1721'


class DirectSyncFixtureBase(TestCase):
    """Writes a course tree to a real temp dir and syncs it directly."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='direct-sync-1721-')
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
        # Reuse the same content_id across repeated calls (editing
        # course.yaml between syncs) so the course identity stays stable
        # — a fresh random content_id per call would make the second
        # write look like a different course.
        if not hasattr(self, '_course_content_id'):
            self._course_content_id = str(uuid.uuid4())
        data = {
            'title': 'Buildcamp',
            'slug': 'buildcamp-1721',
            'required_level': 0,
            'content_id': self._course_content_id,
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

    def _rmtree(self, rel_path):
        shutil.rmtree(os.path.join(self.course_dir, rel_path))

    def _errors_text(self, stats):
        return ' '.join(e.get('error', '') for e in stats['errors'])


class ModuleTransitionClaimedUnitsTest(DirectSyncFixtureBase):
    """Scenarios 1-3: a module keeping its slug while gaining submodules
    this sync, with all of its pre-existing direct units claimed by
    content found elsewhere in the course tree."""

    def setUp(self):
        super().setUp()
        self._write_course_yaml()

    def test_module_keeps_slug_and_gains_submodules_units_all_claimed(self):
        c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
        self._write_yaml('01-monitoring/module.yaml', {'title': 'Monitoring'})
        self._write_markdown(
            '01-monitoring/01-metrics.md',
            {'title': 'Metrics', 'content_id': c1}, 'Body.\n',
        )
        self._write_markdown(
            '01-monitoring/02-alerts.md',
            {'title': 'Alerts', 'content_id': c2}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        unit1_pk = Unit.objects.get(content_id=c1).pk
        unit2_pk = Unit.objects.get(content_id=c2).pk

        # Restructure: same slug ("monitoring"), same directory, but the
        # flat units move into one new submodule that claims both.
        self._remove('01-monitoring/01-metrics.md')
        self._remove('01-monitoring/02-alerts.md')
        self._write_yaml(
            '01-monitoring/01-overview/module.yaml', {'title': 'Overview'},
        )
        self._write_markdown(
            '01-monitoring/01-overview/01-metrics.md',
            {'title': 'Metrics', 'content_id': c1}, 'Body.\n',
        )
        self._write_markdown(
            '01-monitoring/01-overview/02-alerts.md',
            {'title': 'Alerts', 'content_id': c2}, 'Body.\n',
        )

        stats = self._sync()

        error_text = self._errors_text(stats)
        self.assertNotIn('already has direct units', error_text)
        self.assertEqual(stats['errors'], [])

        monitoring = Module.objects.get(
            course__slug='buildcamp-1721', slug='monitoring', parent=None,
        )
        self.assertFalse(monitoring.units.exists())
        self.assertEqual(monitoring.children.count(), 1)

        overview = monitoring.children.get(slug='overview')
        unit1 = Unit.objects.get(content_id=c1)
        unit2 = Unit.objects.get(content_id=c2)
        self.assertEqual(unit1.pk, unit1_pk)
        self.assertEqual(unit2.pk, unit2_pk)
        self.assertEqual(unit1.module_id, overview.pk)
        self.assertEqual(unit2.module_id, overview.pk)

    def test_module_keeps_slug_transition_preserves_progress(self):
        c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
        user = User.objects.create_user(
            email='learner@example.com', password='pw12345',
        )
        self._write_yaml('01-monitoring/module.yaml', {'title': 'Monitoring'})
        self._write_markdown(
            '01-monitoring/01-metrics.md',
            {'title': 'Metrics', 'content_id': c1}, 'Body.\n',
        )
        self._write_markdown(
            '01-monitoring/02-alerts.md',
            {'title': 'Alerts', 'content_id': c2}, 'Body.\n',
        )
        self._sync()

        metrics_unit = Unit.objects.get(content_id=c1)
        completed_at = timezone.now()
        UserCourseProgress.objects.create(
            user=user, unit=metrics_unit, completed_at=completed_at,
        )

        self._remove('01-monitoring/01-metrics.md')
        self._remove('01-monitoring/02-alerts.md')
        self._write_yaml(
            '01-monitoring/01-overview/module.yaml', {'title': 'Overview'},
        )
        self._write_markdown(
            '01-monitoring/01-overview/01-metrics.md',
            {'title': 'Metrics', 'content_id': c1}, 'Body.\n',
        )
        self._write_markdown(
            '01-monitoring/01-overview/02-alerts.md',
            {'title': 'Alerts', 'content_id': c2}, 'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        progress = UserCourseProgress.objects.get(
            user=user, unit_id=metrics_unit.pk,
        )
        self.assertEqual(progress.completed_at, completed_at)
        metrics_unit.refresh_from_db()
        overview = Module.objects.get(
            course__slug='buildcamp-1721', slug='overview',
        )
        self.assertEqual(metrics_unit.module_id, overview.pk)

    def test_module_keeps_slug_transition_units_split_across_two_submodules(self):
        c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
        self._write_yaml('02-evaluation/module.yaml', {'title': 'Evaluation'})
        self._write_markdown(
            '02-evaluation/01-metrics.md',
            {'title': 'Metrics', 'content_id': c1}, 'Body.\n',
        )
        self._write_markdown(
            '02-evaluation/02-rubric.md',
            {'title': 'Rubric', 'content_id': c2}, 'Body.\n',
        )
        self._sync()

        self._remove('02-evaluation/01-metrics.md')
        self._remove('02-evaluation/02-rubric.md')
        self._write_yaml(
            '02-evaluation/01-metrics-sub/module.yaml', {'title': 'Metrics'},
        )
        self._write_markdown(
            '02-evaluation/01-metrics-sub/01-metrics.md',
            {'title': 'Metrics', 'content_id': c1}, 'Body.\n',
        )
        self._write_yaml(
            '02-evaluation/02-rubric-sub/module.yaml', {'title': 'Rubric'},
        )
        self._write_markdown(
            '02-evaluation/02-rubric-sub/01-rubric.md',
            {'title': 'Rubric', 'content_id': c2}, 'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        evaluation = Module.objects.get(
            course__slug='buildcamp-1721', slug='evaluation', parent=None,
        )
        self.assertFalse(evaluation.units.exists())
        self.assertEqual(evaluation.children.count(), 2)

        metrics_sub = evaluation.children.get(slug='metrics-sub')
        rubric_sub = evaluation.children.get(slug='rubric-sub')
        self.assertEqual(Unit.objects.get(content_id=c1).module_id, metrics_sub.pk)
        self.assertEqual(Unit.objects.get(content_id=c2).module_id, rubric_sub.pk)


class ModuleTransitionOrphanedUnitTest(DirectSyncFixtureBase):
    """Scenario 4: a genuinely orphaned direct unit (no matching
    content_id anywhere in the course) inside an otherwise-migrated
    transitioning module.

    Demonstrated together with an unrelated, earlier walk error (a module
    slug collision, processed first in directory-scan order) because that
    is precisely the condition under which the orphaned unit survives
    long enough to be named: once any error is recorded earlier in the
    walk, this module's own immediate stale-unit sweep is suppressed
    (Bug B), so the orphaned unit is still attached when the post
    -submodule-loop full_clean() re-check runs (Bug A) and names it,
    instead of the unmodified #1681 immediate sweep quietly deleting it
    with no error at all (the behaviour reaffirmed by
    ``test_1681_leaf_to_parent_drop_still_works`` below).
    """

    def setUp(self):
        super().setUp()
        self._write_course_yaml()

    def test_module_keeps_slug_transition_with_genuinely_orphaned_unit(self):
        ca, cb, corphan = (
            str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()),
        )
        # An unrelated module pair that will collide on slug on the
        # second sync — sorted before "01-transition" so its error is
        # recorded before the transitioning module is processed.
        self._write_yaml('00-topics-a/module.yaml', {'title': 'Topics A'})
        self._write_markdown(
            '00-topics-a/01-lesson.md', {'title': 'Topics A lesson'}, 'Body.\n',
        )
        self._write_yaml('00-topics-b/module.yaml', {'title': 'Topics B'})
        self._write_markdown(
            '00-topics-b/01-lesson.md', {'title': 'Topics B lesson'}, 'Body.\n',
        )

        self._write_yaml('01-transition/module.yaml', {'title': 'Transition'})
        self._write_markdown(
            '01-transition/01-a.md', {'title': 'A', 'content_id': ca}, 'Body.\n',
        )
        self._write_markdown(
            '01-transition/02-b.md', {'title': 'B', 'content_id': cb}, 'Body.\n',
        )
        self._write_markdown(
            '01-transition/03-orphan.md',
            {'title': 'Orphan', 'content_id': corphan}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        # Introduce the collision (forces an error before "01-transition"
        # is processed this run).
        self._write_yaml(
            '00-topics-b/module.yaml',
            {'title': 'Topics B', 'slug': 'topics-a'},
        )

        # Restructure "01-transition": a and b move into a new submodule;
        # the orphan file is simply removed, with no replacement anywhere.
        self._remove('01-transition/01-a.md')
        self._remove('01-transition/02-b.md')
        self._remove('01-transition/03-orphan.md')
        self._write_yaml(
            '01-transition/01-sub/module.yaml', {'title': 'Sub'},
        )
        self._write_markdown(
            '01-transition/01-sub/01-a.md', {'title': 'A', 'content_id': ca}, 'Body.\n',
        )
        self._write_markdown(
            '01-transition/01-sub/02-b.md', {'title': 'B', 'content_id': cb}, 'Body.\n',
        )

        stats = self._sync()

        error_text = self._errors_text(stats)
        self.assertIn('topics-a', error_text.lower())

        # Exactly one error names the transitioning module and the
        # orphaned unit.
        orphan_errors = [
            e for e in stats['errors']
            if 'Transition' in e.get('error', '') and 'Orphan' in e.get('error', '')
        ]
        self.assertEqual(len(orphan_errors), 1)

        transition = Module.objects.get(
            course__slug='buildcamp-1721', slug='transition', parent=None,
        )
        sub = transition.children.get(slug='sub')
        self.assertEqual(Unit.objects.get(content_id=ca).module_id, sub.pk)
        self.assertEqual(Unit.objects.get(content_id=cb).module_id, sub.pk)

        # The orphaned unit was not deleted, and is still attached to the
        # (now also-parent) transitioning module.
        orphan_unit = Unit.objects.get(content_id=corphan)
        self.assertEqual(orphan_unit.module_id, transition.pk)


class SiblingRenamedAndTransitioningModuleTest(DirectSyncFixtureBase):
    """Scenario 5: a re-slugged sibling module and a same-slug
    transitioning module resolved correctly in one sync (the real
    buildcamp shape: most weeks re-slugged, two weeks kept their slug)."""

    def setUp(self):
        super().setUp()
        self._write_course_yaml()

    def test_sibling_module_new_slug_and_transitioning_module_same_slug_coexist(self):
        c_topic, c_quiz, c_essay = (
            str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()),
        )
        self._write_yaml('01-topics/module.yaml', {'title': 'Topics'})
        self._write_markdown(
            '01-topics/01-lesson.md',
            {'title': 'Lesson', 'content_id': c_topic}, 'Body.\n',
        )
        self._write_yaml('02-grading/module.yaml', {'title': 'Grading'})
        self._write_markdown(
            '02-grading/01-quiz.md',
            {'title': 'Quiz', 'content_id': c_quiz}, 'Body.\n',
        )
        self._write_markdown(
            '02-grading/02-essay.md',
            {'title': 'Essay', 'content_id': c_essay}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        topic_pk = Unit.objects.get(content_id=c_topic).pk

        # Module A: brand-new directory/slug (old "topics" dir removed
        # entirely, replaced by "concepts").
        self._rmtree('01-topics')
        self._write_yaml('01-concepts/module.yaml', {'title': 'Concepts'})
        self._write_markdown(
            '01-concepts/01-lesson.md',
            {'title': 'Lesson', 'content_id': c_topic}, 'Body.\n',
        )

        # Module B: keeps its slug, gains submodules (#1721 shape).
        self._remove('02-grading/01-quiz.md')
        self._remove('02-grading/02-essay.md')
        self._write_yaml('02-grading/01-quiz-sub/module.yaml', {'title': 'Quiz'})
        self._write_markdown(
            '02-grading/01-quiz-sub/01-quiz.md',
            {'title': 'Quiz', 'content_id': c_quiz}, 'Body.\n',
        )
        self._write_yaml('02-grading/02-essay-sub/module.yaml', {'title': 'Essay'})
        self._write_markdown(
            '02-grading/02-essay-sub/01-essay.md',
            {'title': 'Essay', 'content_id': c_essay}, 'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        self.assertFalse(
            Module.objects.filter(
                course__slug='buildcamp-1721', slug='topics',
            ).exists(),
        )
        concepts = Module.objects.get(
            course__slug='buildcamp-1721', slug='concepts',
        )
        topic_unit = Unit.objects.get(content_id=c_topic)
        self.assertEqual(topic_unit.pk, topic_pk)
        self.assertEqual(topic_unit.module_id, concepts.pk)

        grading = Module.objects.get(
            course__slug='buildcamp-1721', slug='grading', parent=None,
        )
        self.assertFalse(grading.units.exists())
        self.assertEqual(grading.children.count(), 2)
        quiz_sub = grading.children.get(slug='quiz-sub')
        essay_sub = grading.children.get(slug='essay-sub')
        self.assertEqual(Unit.objects.get(content_id=c_quiz).module_id, quiz_sub.pk)
        self.assertEqual(Unit.objects.get(content_id=c_essay).module_id, essay_sub.pk)


class WalkErrorSuppressesSweepsTest(DirectSyncFixtureBase):
    """Scenarios 6-9: the shared walk-error gate on the three destructive
    sweeps."""

    def setUp(self):
        super().setUp()
        self._write_course_yaml()

    def _build_baseline(self):
        # An unrelated module pair, clean for now (will collide later).
        self._write_yaml('00-topics-a/module.yaml', {'title': 'Topics A'})
        self._write_markdown(
            '00-topics-a/01-lesson.md', {'title': 'Topics A lesson'}, 'Body.\n',
        )
        self._write_yaml('00-topics-b/module.yaml', {'title': 'Topics B'})
        self._write_markdown(
            '00-topics-b/01-lesson.md', {'title': 'Topics B lesson'}, 'Body.\n',
        )
        # A module with a unit that will become genuinely stale.
        c_stay, c_gone = str(uuid.uuid4()), str(uuid.uuid4())
        self._write_yaml('01-keep/module.yaml', {'title': 'Keep'})
        self._write_markdown(
            '01-keep/01-stay.md', {'title': 'Stay', 'content_id': c_stay}, 'Body.\n',
        )
        self._write_markdown(
            '01-keep/02-gone.md', {'title': 'Gone', 'content_id': c_gone}, 'Body.\n',
        )
        # A whole module that will become genuinely stale.
        self._write_yaml('02-stale-module/module.yaml', {'title': 'Stale module'})
        self._write_markdown(
            '02-stale-module/01-lesson.md', {'title': 'Only lesson'}, 'Body.\n',
        )
        return c_stay, c_gone

    def test_walk_error_suppresses_all_three_sweeps(self):
        c_stay, c_gone = self._build_baseline()
        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        # Introduce the collision (processed before "01-keep"/
        # "02-stale-module" this run).
        self._write_yaml(
            '00-topics-b/module.yaml',
            {'title': 'Topics B', 'slug': 'topics-a'},
        )
        # Genuinely stale unit and genuinely stale module.
        self._remove('01-keep/02-gone.md')
        self._rmtree('02-stale-module')

        stats = self._sync()

        error_text = self._errors_text(stats)
        self.assertIn('topics-a', error_text.lower())

        # Nothing was deleted by any of the three sweeps.
        self.assertEqual(stats['deleted'], 0)
        self.assertTrue(Unit.objects.filter(content_id=c_gone).exists())
        self.assertTrue(
            Module.objects.filter(
                course__slug='buildcamp-1721', slug='stale-module',
            ).exists(),
        )
        self.assertTrue(Unit.objects.filter(content_id=c_stay).exists())

        # Exactly one additional suppression-summary error.
        suppression_errors = [
            e for e in stats['errors']
            if 'stale-content sweep was skipped' in e.get('error', '')
        ]
        self.assertEqual(len(suppression_errors), 1)

    def test_walk_with_no_errors_still_sweeps_normally(self):
        c_stay, c_gone = self._build_baseline()
        self._sync()

        # No collision introduced this time — clean re-sync.
        self._remove('01-keep/02-gone.md')
        self._rmtree('02-stale-module')

        stats = self._sync()

        self.assertEqual(stats['errors'], [])
        self.assertFalse(Unit.objects.filter(content_id=c_gone).exists())
        self.assertTrue(Unit.objects.filter(content_id=c_stay).exists())
        self.assertFalse(
            Module.objects.filter(
                course__slug='buildcamp-1721', slug='stale-module',
            ).exists(),
        )
        self.assertGreater(stats['deleted'], 0)

    def test_pre_walk_errors_do_not_suppress_sweeps(self):
        c_gone = str(uuid.uuid4())
        self._write_yaml('01-keep/module.yaml', {'title': 'Keep'})
        self._write_markdown(
            '01-keep/01-stay.md', {'title': 'Stay'}, 'Body.\n',
        )
        self._write_markdown(
            '01-keep/02-gone.md', {'title': 'Gone', 'content_id': c_gone}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        # Malformed `instructors:` — an error recorded in
        # _sync_course_children, BEFORE _sync_course_modules runs.
        self._write_course_yaml(instructors='not-a-list')
        self._remove('01-keep/02-gone.md')

        stats = self._sync()

        error_text = self._errors_text(stats)
        self.assertIn('instructors', error_text.lower())
        # The pre-walk error alone must not suppress the sweep.
        self.assertFalse(Unit.objects.filter(content_id=c_gone).exists())

    def test_error_recorded_partway_through_walk_only_suppresses_later_sweeps(self):
        c1_stay, c1_gone = str(uuid.uuid4()), str(uuid.uuid4())
        self._write_yaml('01-first/module.yaml', {'title': 'First'})
        self._write_markdown(
            '01-first/01-stay.md',
            {'title': 'Stay', 'content_id': c1_stay}, 'Body.\n',
        )
        self._write_markdown(
            '01-first/02-gone.md',
            {'title': 'Gone', 'content_id': c1_gone}, 'Body.\n',
        )
        self._write_yaml('02-collision-a/module.yaml', {'title': 'Collision A'})
        self._write_markdown(
            '02-collision-a/01-lesson.md', {'title': 'CA lesson'}, 'Body.\n',
        )
        self._write_yaml('03-collision-b/module.yaml', {'title': 'Collision B'})
        self._write_markdown(
            '03-collision-b/01-lesson.md', {'title': 'CB lesson'}, 'Body.\n',
        )
        self._write_yaml('04-stale-module/module.yaml', {'title': 'Stale module'})
        self._write_markdown(
            '04-stale-module/01-lesson.md', {'title': 'Only lesson'}, 'Body.\n',
        )
        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        # First module's own stale unit disappears — its own immediate
        # sweep runs before any error exists this run.
        self._remove('01-first/02-gone.md')
        # A later module collides, recording an error after "01-first"
        # was already processed cleanly.
        self._write_yaml(
            '03-collision-b/module.yaml',
            {'title': 'Collision B', 'slug': 'collision-a'},
        )
        # A whole module goes stale — only caught by the course-end
        # module sweep, gated on the walk as a whole.
        self._rmtree('04-stale-module')

        stats = self._sync()

        error_text = self._errors_text(stats)
        self.assertIn('collision-a', error_text.lower())

        # The earlier, clean module's own immediate sweep still ran.
        self.assertFalse(Unit.objects.filter(content_id=c1_gone).exists())
        self.assertTrue(Unit.objects.filter(content_id=c1_stay).exists())

        # The course-end sweeps are suppressed because the walk as a
        # whole recorded an error.
        self.assertTrue(
            Module.objects.filter(
                course__slug='buildcamp-1721', slug='stale-module',
            ).exists(),
        )


class LeafToParentDropStillWorksTest(DirectSyncFixtureBase):
    """Scenario 10: re-affirms the pre-existing #1674/#1681 leaf-to
    -parent transition where old direct units are genuinely dropped (no
    match anywhere in the new tree) — submodules are still created and
    the dropped units are still deleted immediately by the unmodified
    precompute-informed sweep, not held hostage by this issue's bypass
    mechanism."""

    def test_1681_leaf_to_parent_drop_still_works(self):
        self._write_course_yaml()
        self._write_yaml('07-transition/module.yaml', {'title': 'Transition'})
        self._write_markdown(
            '07-transition/01-old-unit.md', {'title': 'Old unit'}, 'Body.\n',
        )
        self._sync()
        self.assertTrue(Unit.objects.filter(slug='old-unit').exists())

        self._remove('07-transition/01-old-unit.md')
        self._write_yaml(
            '07-transition/01-new-sub/module.yaml', {'title': 'New sub'},
        )
        self._write_markdown(
            '07-transition/01-new-sub/01-lesson.md', {'title': 'New lesson'}, 'Body.\n',
        )

        stats = self._sync()

        self.assertEqual(stats['errors'], [])
        transition = Module.objects.get(
            course__slug='buildcamp-1721', slug='transition',
        )
        self.assertFalse(Unit.objects.filter(slug='old-unit').exists())
        self.assertEqual(
            list(transition.children.values_list('slug', flat=True)),
            ['new-sub'],
        )
