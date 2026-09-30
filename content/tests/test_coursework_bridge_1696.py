"""Cohort bridge onto ``cb_curriculum.Cohort`` (#1696 phase 4).

Every AISL cohort gets one curriculum mirror that coursework projects,
submissions and enrollments hang off. These tests pin the mapping rules,
idempotency, the backfill migration and the learner-invisible install
(no package Studio section, no member API routes).
"""

import datetime
import importlib
from types import SimpleNamespace

from community_base.api.registry import routes
from community_base.curriculum.models import Cohort as CurriculumCohort
from community_base.curriculum.models import Enrollment as CurriculumEnrollment
from community_base.studio.registry import sections
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course
from content.services.coursework_bridge import (
    SELF_PACED_SLUG,
    ensure_curriculum_cohort,
    ensure_curriculum_enrollment,
    review_path,
)
from content.sync_parsers.families.courses import _reattach_course_fks

User = get_user_model()

backfill = importlib.import_module(
    'content.migrations.0082_backfill_cohort_curriculum_mirrors',
)


def _mirror(cohort):
    cohort.refresh_from_db()
    return cohort.curriculum_cohort


class CohortMirrorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
        )
        cls.today = timezone.localdate()

    def _dated(self, **overrides):
        values = {
            'course': self.course,
            'name': 'Cohort 4',
            'mode': 'cohort',
            'start_date': self.today,
            'end_date': self.today + datetime.timedelta(days=56),
        }
        values.update(overrides)
        return Cohort.objects.create(**values)

    def test_dated_cohort_mirror_copies_fields(self):
        cohort = self._dated(external_key='4', max_participants=60)

        mirror = _mirror(cohort)

        self.assertEqual(mirror.course_id, self.course.pk)
        self.assertEqual(mirror.slug, '4')
        self.assertEqual(mirror.title, 'Cohort 4')
        self.assertEqual(mirror.mode, 'cohort')
        self.assertEqual(mirror.start_date, cohort.start_date)
        self.assertEqual(mirror.end_date, cohort.end_date)
        self.assertEqual(mirror.max_participants, 60)
        self.assertTrue(mirror.visible)
        self.assertFalse(mirror.finished)
        self.assertEqual(mirror.project_passing_score, 0)

    def test_self_paced_cohort_without_key_uses_self_paced_slug(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Learn at your pace', mode='self_paced',
        )

        mirror = _mirror(cohort)

        self.assertEqual(mirror.slug, SELF_PACED_SLUG)
        self.assertEqual(mirror.mode, 'self_paced')
        self.assertIsNone(mirror.start_date)
        self.assertFalse(mirror.finished)

    def test_dated_cohort_without_key_slugifies_its_name(self):
        self.assertEqual(_mirror(self._dated(name='March 2026 Cohort')).slug, 'march-2026-cohort')

    def test_slug_collision_in_course_appends_cohort_id(self):
        self._dated(name='Spring')
        second = self._dated(name='Spring')

        self.assertEqual(_mirror(second).slug, f'spring-{second.pk}')

    def test_update_changes_mirror_in_place_and_keeps_passing_score(self):
        cohort = self._dated(external_key='4')
        mirror = _mirror(cohort)
        mirror.project_passing_score = 7
        mirror.save(update_fields=['project_passing_score'])

        cohort.name = 'Cohort 4 (renamed)'
        cohort.start_date = self.today - datetime.timedelta(days=60)
        cohort.end_date = self.today - datetime.timedelta(days=1)
        cohort.is_active = False
        cohort.save()

        updated = _mirror(cohort)
        self.assertEqual(updated.pk, mirror.pk)
        self.assertEqual(updated.title, 'Cohort 4 (renamed)')
        self.assertEqual(updated.end_date, cohort.end_date)
        self.assertFalse(updated.visible)
        self.assertTrue(updated.finished)
        self.assertEqual(updated.project_passing_score, 7)

    def test_ensure_is_idempotent(self):
        cohort = self._dated()
        first = ensure_curriculum_cohort(cohort)

        with self.assertNumQueries(4):
            # Savepoint, locked mirror read, slug check, release: no write.
            second = ensure_curriculum_cohort(cohort)

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(CurriculumCohort.objects.filter(course=self.course).count(), 1)

    def test_deleting_an_unused_cohort_removes_its_mirror(self):
        cohort = self._dated()
        mirror_id = _mirror(cohort).pk

        cohort.delete()

        self.assertFalse(CurriculumCohort.objects.filter(pk=mirror_id).exists())

    def test_mirror_with_enrollments_survives_cohort_deletion(self):
        cohort = self._dated()
        user = User.objects.create_user(email='learner@test.com')
        enrollment = ensure_curriculum_enrollment(user, cohort)

        cohort.delete()

        self.assertTrue(CurriculumEnrollment.objects.filter(pk=enrollment.pk).exists())

    def test_deleting_the_course_deletes_cohorts_and_mirrors(self):
        cohort = self._dated()
        mirror_id = _mirror(cohort).pk

        self.course.delete()

        self.assertFalse(Cohort.objects.filter(pk=cohort.pk).exists())
        self.assertFalse(CurriculumCohort.objects.filter(pk=mirror_id).exists())

    def test_reattached_cohort_mirror_follows_the_target_course(self):
        orphan = Course.objects.create(title='Old', slug='old-slug', status='published')
        cohort = self._dated(course=orphan, external_key='4')

        _reattach_course_fks(orphan, self.course)
        orphan.delete()

        mirror = _mirror(cohort)
        self.assertEqual(mirror.course_id, self.course.pk)
        self.assertEqual(mirror.slug, '4')


class CurriculumEnrollmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(title='AI Buildcamp', slug='ai-buildcamp')
        cls.cohort = Cohort.objects.create(course=course, name='Self-paced', mode='self_paced')
        cls.user = User.objects.create_user(email='learner@test.com')

    def test_creates_one_active_enrollment_per_user_and_mirror(self):
        first = ensure_curriculum_enrollment(self.user, self.cohort)
        again = ensure_curriculum_enrollment(self.user, self.cohort)

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(first.cohort_id, _mirror(self.cohort).pk)
        self.assertIsNone(first.unenrolled_at)

    def test_unenrolled_learner_gets_a_new_active_enrollment(self):
        first = ensure_curriculum_enrollment(self.user, self.cohort)
        first.unenrolled_at = timezone.now()
        first.save(update_fields=['unenrolled_at'])

        second = ensure_curriculum_enrollment(self.user, self.cohort)

        self.assertNotEqual(second.pk, first.pk)
        self.assertIsNone(second.unenrolled_at)


class BackfillMigrationTest(TestCase):
    """``content.0082`` gives every pre-existing cohort a mirror."""

    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.course = Course.objects.create(title='AI Buildcamp', slug='ai-buildcamp')
        cls.cohorts = [
            Cohort.objects.create(
                course=cls.course, name='Cohort 3', external_key='3',
                start_date=today - datetime.timedelta(days=120),
                end_date=today - datetime.timedelta(days=60),
            ),
            Cohort.objects.create(course=cls.course, name='Self-paced', mode='self_paced'),
            Cohort.objects.create(
                course=cls.course, name='Spring', start_date=today, end_date=today,
            ),
        ]

    def setUp(self):
        # The state before the migration: cohorts without mirrors.
        Cohort.objects.update(curriculum_cohort=None)
        CurriculumCohort.objects.all().delete()

    def _run(self):
        backfill.create_mirrors(apps, SimpleNamespace(connection=connection))

    def test_every_cohort_gets_a_matching_mirror(self):
        self._run()

        mirrors = {
            cohort.name: cohort.curriculum_cohort
            for cohort in Cohort.objects.select_related('curriculum_cohort')
        }
        self.assertEqual(
            {name: (m.slug, m.mode, m.finished) for name, m in mirrors.items()},
            {
                'Cohort 3': ('3', 'cohort', True),
                'Self-paced': (SELF_PACED_SLUG, 'self_paced', False),
                'Spring': ('spring', 'cohort', False),
            },
        )
        # The runtime rules agree with the frozen migration copy.
        for cohort in Cohort.objects.all():
            self.assertEqual(ensure_curriculum_cohort(cohort).pk, cohort.curriculum_cohort_id)

    def test_rerun_creates_nothing(self):
        self._run()
        self._run()

        self.assertEqual(CurriculumCohort.objects.count(), len(self.cohorts))

    def test_reverse_removes_unused_mirrors(self):
        self._run()

        backfill.remove_mirrors(apps, SimpleNamespace(connection=connection))

        self.assertEqual(CurriculumCohort.objects.count(), 0)
        self.assertFalse(Cohort.objects.filter(curriculum_cohort__isnull=False).exists())


class CourseworkInstallSurfaceTest(TestCase):
    """Coursework is installed for models and jobs only."""

    def test_package_studio_section_is_not_registered(self):
        self.assertNotIn('coursework', [section.slug for section in sections()])

    def test_package_member_api_routes_are_not_registered(self):
        paths = [route.path for route in routes()]
        self.assertFalse([path for path in paths if 'enrollment-preferences' in path])
        self.assertFalse([path for path in paths if 'leaderboard' in path])


class ReviewPathTest(TestCase):
    def test_review_links_are_site_relative_aisl_routes(self):
        course = Course.objects.create(title='AI Buildcamp', slug='ai-buildcamp')
        cohort = Cohort.objects.create(course=course, name='Self-paced', mode='self_paced')
        project = _mirror(cohort).projects.create(slug='attempt-1', title='Attempt 1')
        review = type('Review', (), {'id': 42})()

        self.assertEqual(
            review_path(project),
            '/courses/ai-buildcamp/projects/attempt-1/reviews',
        )
        self.assertEqual(
            review_path(project, review),
            '/courses/ai-buildcamp/projects/attempt-1/reviews/42',
        )
