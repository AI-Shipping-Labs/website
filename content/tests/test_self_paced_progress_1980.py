"""Viewer-specific Self-paced progress and navigation (issue #1980)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.access import get_user_level
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Enrollment,
    Module,
    Unit,
    UserCourseProgress,
)
from content.services.course_schedule import select_display_cohort
from content.views.home import _get_in_progress_courses

User = get_user_model()


class SelfPacedProgressTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Event-first course', slug='ai-buildcamp',
            status='published', required_level=0,
        )
        cls.first_module = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundations',
            sort_order=1,
        )
        first_session = Module.objects.create(
            course=cls.course, parent=cls.first_module,
            title='Session 1', slug='session', sort_order=0,
        )
        first_overview = Module.objects.create(
            course=cls.course, parent=cls.first_module,
            title='Week 1 Overview', slug='overview', sort_order=1,
        )
        cls.event_first = cls._unit(
            first_session, 'Session 1', 'live-kickoff', 0, 'event',
        )
        cls.first_lesson = cls._unit(
            first_overview, 'First real lesson', 'first-real-lesson', 0, 'lesson',
        )
        cls.second_module = Module.objects.create(
            course=cls.course, title='Build', slug='build', sort_order=2,
        )
        second_session = Module.objects.create(
            course=cls.course, parent=cls.second_module,
            title='Session 2', slug='session', sort_order=0,
        )
        second_overview = Module.objects.create(
            course=cls.course, parent=cls.second_module,
            title='Testing and Evaluation Overview', slug='overview', sort_order=1,
        )
        cls.event_middle = cls._unit(
            second_session, 'Session 2', 'live-review', 0, 'event',
        )
        cls.second_lesson = cls._unit(
            second_overview, 'Second real lesson', 'second-real-lesson', 0, 'lesson',
        )
        cls.optional_module = Module.objects.create(
            course=cls.course, title='Optional', slug='optional',
            sort_order=3, is_bonus=True,
        )
        cls.optional_lesson = cls._unit(
            cls.optional_module, 'Optional deep dive', 'optional-deep-dive', 0, 'lesson',
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', external_key='self-paced',
            mode='self_paced', is_active=True,
        )
        today = timezone.localdate()
        cls.current_dated = Cohort.objects.create(
            course=cls.course, name='Current cohort', external_key='current',
            mode='cohort', is_active=True,
            start_date=today - timedelta(days=2),
            end_date=today + timedelta(days=20),
        )
        cls.historical = Cohort.objects.create(
            course=cls.course, name='Past cohort', external_key='past',
            mode='cohort', is_active=True,
            start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=30),
        )
        cls.self_paced_user = cls._learner('self-paced')
        cls.dated_user = cls._learner('dated')
        cls.alumnus = cls._learner('alumnus')
        cls._enroll(cls.self_paced_user, cls.self_paced)
        cls._enroll(cls.dated_user, cls.self_paced, cls.current_dated)
        cls._enroll(cls.alumnus, cls.historical, cls.self_paced)

    @classmethod
    def _unit(cls, module, title, slug, order, kind):
        return Unit.objects.create(
            module=module, title=title, slug=slug,
            sort_order=order, kind=kind,
        )

    @classmethod
    def _learner(cls, label):
        user = User.objects.create_user(
            email=f'{label}-progress-1980@test.com', password='pw',
            email_verified=True,
        )
        Enrollment.objects.create(user=user, course=cls.course)
        return user

    @classmethod
    def _enroll(cls, user, *cohorts):
        CohortEnrollment.objects.bulk_create([
            CohortEnrollment(user=user, cohort=cohort) for cohort in cohorts
        ])

    def _get_as(self, user, path):
        self.client.force_login(user)
        return self.client.get(path)

    def _complete(self, user, *units):
        UserCourseProgress.objects.bulk_create([
            UserCourseProgress(user=user, unit=unit, completed_at=timezone.now())
            for unit in units
        ])

    def test_self_paced_flow_skips_events_across_member_surfaces(self):
        home = self._get_as(self.self_paced_user, '/courses/ai-buildcamp/home')
        self.assertEqual(home.context['core_total'], 2)
        self.assertEqual(home.context['recommended_unit'], self.first_lesson)
        self.assertEqual(home.context['current_module']['module'], self.first_module)

        overview = self._get_as(
            self.self_paced_user, '/courses/ai-buildcamp?view=overview',
        )
        self.assertEqual(overview.context['next_unit_for_user'], self.first_lesson)

        syllabus = self._get_as(
            self.self_paced_user, '/courses/ai-buildcamp/home/syllabus',
        )
        self.assertNotContains(syllabus, self.event_first.title)
        self.assertNotContains(syllabus, self.event_middle.title)
        self.assertContains(syllabus, self.first_lesson.title)

        reader = self._get_as(self.self_paced_user, self.first_lesson.get_absolute_url())
        self.assertIsNone(reader.context['prev_unit'])
        self.assertEqual(reader.context['next_unit'], self.second_lesson)
        self.assertEqual(reader.context['reader_progress_total'], 2)
        self.assertNotContains(reader, self.event_first.title)
        self.assertNotContains(reader, self.event_middle.title)

    def test_finishing_real_units_completes_home_and_removes_dashboard_card(self):
        self._complete(
            self.self_paced_user, self.first_lesson, self.second_lesson,
        )

        home = self._get_as(self.self_paced_user, '/courses/ai-buildcamp/home')
        self.assertEqual(home.context['core_completed'], 2)
        self.assertEqual(home.context['action'], 'complete')
        self.assertIsNone(home.context['recommended_unit'])

        cards = _get_in_progress_courses(
            self.self_paced_user, get_user_level(self.self_paced_user),
        )
        self.assertNotIn(self.course, [item['course'] for item in cards])

    def test_current_dated_enrollment_keeps_event_progress_and_navigation(self):
        cohort, is_preview = select_display_cohort(self.course, self.dated_user)
        self.assertEqual((cohort, is_preview), (self.current_dated, False))

        syllabus = self._get_as(
            self.dated_user, '/courses/ai-buildcamp/home/syllabus',
        )
        self.assertFalse(syllabus.context['self_paced_view'])
        self.assertContains(syllabus, self.event_first.title)

        reader = self._get_as(self.dated_user, self.first_lesson.get_absolute_url())
        self.assertEqual(reader.context['prev_unit'], self.event_first)
        self.assertEqual(reader.context['next_unit'], self.event_middle)
        self.assertEqual(reader.context['reader_progress_total'], 4)
        self.assertContains(reader, self.event_first.title)
        self.assertContains(reader, self.event_middle.title)

        overview = self._get_as(
            self.dated_user, '/courses/ai-buildcamp?view=overview',
        )
        self.assertEqual(overview.context['next_unit_for_user'], self.event_first)

    def test_returning_alumnus_uses_self_paced_default_everywhere(self):
        cohort, is_preview = select_display_cohort(self.course, self.alumnus)
        self.assertEqual((cohort, is_preview), (self.self_paced, False))

        home = self._get_as(self.alumnus, '/courses/ai-buildcamp/home')
        self.assertTrue(home.context['self_paced_view'])
        self.assertEqual(home.context['core_total'], 2)
        self.assertNotContains(home, self.event_first.title)

        reader = self._get_as(self.alumnus, self.first_lesson.get_absolute_url())
        self.assertIsNone(reader.context['prev_unit'])
        self.assertEqual(reader.context['next_unit'], self.second_lesson)

    def test_authenticated_api_progress_matches_default_view_and_keeps_syllabus(self):
        self._complete(self.self_paced_user, self.first_lesson)
        self._complete(self.dated_user, self.first_lesson)

        self_paced = self._get_as(
            self.self_paced_user, '/api/courses/ai-buildcamp',
        ).json()
        dated = self._get_as(
            self.dated_user, '/api/courses/ai-buildcamp',
        ).json()

        self.assertEqual(self_paced['progress'], {'completed': 1, 'total': 2})
        self.assertEqual(dated['progress'], {'completed': 1, 'total': 4})
        self.assertEqual(self_paced.keys(), dated.keys())
        self.assertEqual(self_paced['syllabus'], dated['syllabus'])
        syllabus_units = self_paced['syllabus'][0]['units']
        self.assertEqual(syllabus_units[0]['kind'], 'event')

    def test_dashboard_next_unit_uses_each_default_cohort_policy(self):
        self_paced_cards = _get_in_progress_courses(
            self.self_paced_user, get_user_level(self.self_paced_user),
        )
        dated_cards = _get_in_progress_courses(
            self.dated_user, get_user_level(self.dated_user),
        )

        self.assertEqual(self_paced_cards[0]['next_unit'], self.first_lesson)
        self.assertEqual(self_paced_cards[0]['total_units'], 2)
        self.assertEqual(dated_cards[0]['next_unit'], self.event_first)
        self.assertEqual(dated_cards[0]['total_units'], 5)
