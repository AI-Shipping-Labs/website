"""Course-home recommendations and the private learner entry path."""

import datetime

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from content.access import LEVEL_MAIN
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseAccess,
    Enrollment,
    Module,
    Unit,
    UserCourseProgress,
)
from content.services.course_home import build_course_home
from events.models import Event, EventSeries


class CourseHomeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='course-home-learner@example.com', password='testpass',
            email_verified=True,
        )
        cls.other = User.objects.create_user(
            email='course-home-other@example.com', password='testpass',
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='A course with real curriculum', slug='course-home-test',
            status='published', required_level=0,
            discussion_url='https://example.org/course-discussion',
        )
        cls.first = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundations',
            sort_order=1, available_after_days=0,
        )
        cls.second = Module.objects.create(
            course=cls.course, title='Build a project', slug='build-project',
            sort_order=2, available_after_days=14,
        )
        cls.optional = Module.objects.create(
            course=cls.course, title='Extra practice', slug='extra-practice',
            sort_order=3, is_bonus=True,
        )
        cls.lesson1 = Unit.objects.create(
            module=cls.first, title='First lesson', slug='first-lesson', sort_order=1,
        )
        cls.lesson2 = Unit.objects.create(
            module=cls.first, title='Second lesson', slug='second-lesson', sort_order=2,
        )
        cls.lesson3 = Unit.objects.create(
            module=cls.second, title='Project walkthrough', slug='project-walkthrough',
        )
        cls.bonus = Unit.objects.create(
            module=cls.optional, title='Bonus lesson', slug='bonus-lesson',
        )
        cls.homework = Unit.objects.create(
            module=cls.first, title='Submit homework', slug='submit-homework',
            kind='homework', sort_order=3,
        )

    def _cohort(self, *, key='owned', start_days=-21, end_days=21, user=None):
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=self.course, name=f'{key} cohort', external_key=key,
            start_date=today + datetime.timedelta(days=start_days),
            end_date=today + datetime.timedelta(days=end_days),
        )
        CohortEnrollment.objects.create(cohort=cohort, user=user or self.user)
        return cohort

    def _complete(self, *units):
        for unit in units:
            UserCourseProgress.objects.create(
                user=self.user, unit=unit, completed_at=timezone.now(),
            )

    def test_first_visit_starts_with_first_core_lesson_and_excludes_optional_and_homework(self):
        model = build_course_home(self.course, self.user, None)
        self.assertEqual(model['action'], 'start')
        self.assertEqual(model['recommended_unit'], self.lesson1)
        self.assertEqual((model['core_completed'], model['core_total']), (0, 3))
        self.assertEqual([row['module'] for row in model['optional_rows']], [self.optional])
        self.assertEqual(model['orientation_rows'], [])
        self.assertEqual(model['core_rows'][0]['module'], self.first)
        self.assertIn(('Course communication', self.course.discussion_url), model['help_links'])

    def test_orientation_uses_authored_module_identity(self):
        self.first.title = 'Course Logistics'
        self.first.save(update_fields=['title'])
        model = build_course_home(self.course, self.user, None)
        self.assertEqual(model['orientation_rows'][0]['module'], self.first)
        self.assertNotIn(self.first, [row['module'] for row in model['core_rows']])

    def test_course_progress_is_shown_on_home_instead_of_the_public_overview(self):
        self._complete(self.lesson1)
        self.client.login(email=self.user.email, password='testpass')

        home = self.client.get('/courses/course-home-test/home')
        overview = self.client.get('/courses/course-home-test')

        self.assertContains(home, 'data-testid="course-home-progress"')
        self.assertContains(home, '1 of 4 completed')
        self.assertContains(home, 'aria-valuemax="4"')
        self.assertContains(overview, 'Syllabus')
        self.assertNotContains(overview, 'Your Progress')
        self.assertNotContains(overview, 'data-testid="course-home-progress"')

    def test_lessons_take_priority_over_unscheduled_sessions(self):
        event = Unit.objects.create(
            module=self.first, title='Session 1', slug='session-1',
            sort_order=3, kind='event',
        )
        self._complete(self.lesson1, self.lesson2)
        model = build_course_home(self.course, self.user, None)
        self.assertEqual(model['recommended_unit'], self.lesson3)
        self.assertEqual(model['recommendation_label'], 'Open lesson')
        self._complete(self.lesson3)
        remaining = build_course_home(self.course, self.user, None)
        self.assertEqual(remaining['recommended_unit'], event)
        self.assertEqual(remaining['recommendation_label'], 'Open session')

    def test_behind_schedule_week_is_separate_from_personal_recommendation(self):
        cohort = self._cohort()
        self._complete(self.lesson1)
        model = build_course_home(self.course, self.user, cohort)
        self.assertEqual(model['current_week'], 4)
        self.assertEqual(model['action'], 'continue')
        self.assertEqual(model['recommended_unit'], self.lesson2)
        self.assertEqual(model['core_completed'], 1)

    def test_locked_material_is_not_recommended_and_completed_core_is_not_certificate(self):
        cohort = self._cohort(start_days=-1)
        self._complete(self.lesson1, self.lesson2)
        locked = build_course_home(self.course, self.user, cohort)
        self.assertEqual(locked['action'], 'locked')
        self.assertIsNone(locked['recommended_unit'])
        self.assertEqual(
            locked['next_available'], cohort.start_date + datetime.timedelta(days=14),
        )
        self._complete(self.lesson3)
        complete = build_course_home(self.course, self.user, cohort)
        self.assertEqual(complete['action'], 'complete')
        self.assertEqual((complete['core_completed'], complete['core_total']), (3, 3))

    def test_anonymous_and_unauthorized_users_never_see_private_home(self):
        url = '/courses/course-home-test/home'
        self.assertRedirects(
            self.client.get(url), f'/accounts/login/?next={url}',
            fetch_redirect_response=False,
        )
        self.course.required_level = LEVEL_MAIN
        self.course.save(update_fields=['required_level'])
        self.client.force_login(self.user)
        self.assertRedirects(
            self.client.get(url), self.course.get_absolute_url(),
            fetch_redirect_response=False,
        )

    def test_owned_cohort_selection_applies_to_page_and_unowned_is_not_visible(self):
        owned = self._cohort(key='cohort-a')
        other = self._cohort(key='cohort-b', user=self.other)
        self.client.force_login(self.user)
        url = '/courses/course-home-test/home'
        response = self.client.get(f'{url}?cohort={owned.external_key}')
        self.assertEqual(response.context['cohort'], owned)
        self.assertContains(response, f'?cohort={owned.external_key}')
        self.assertNotContains(response, other.name)
        self.assertEqual(self.client.get(f'{url}?cohort={other.external_key}').status_code, 404)
        self.assertEqual(self.client.get(f'{url}?cohort=missing').status_code, 404)

    def test_public_preview_is_not_adopted_as_an_unenrolled_learners_cohort(self):
        course = Course.objects.create(
            title='Buildcamp preview', slug='ai-buildcamp', status='published',
            required_level=0,
        )
        Cohort.objects.create(
            course=course, name='Public preview', external_key='public-preview',
            start_date=timezone.localdate() - datetime.timedelta(days=1),
            end_date=timezone.localdate() + datetime.timedelta(days=30),
        )
        self.client.force_login(self.user)
        response = self.client.get('/courses/ai-buildcamp/home')
        self.assertIsNone(response.context['cohort'])
        self.assertNotContains(response, 'Public preview')

    def test_unlinked_buildcamp_learner_gets_foundations_and_work_without_cohort_data(self):
        course = Course.objects.create(
            title='AI Engineering Buildcamp', slug='ai-buildcamp',
            status='published', required_level=0,
        )
        logistics = Module.objects.create(
            course=course, title='Course Logistics', slug='course-logistics',
            sort_order=0,
        )
        foundations = Module.objects.create(
            course=course, title='Foundations', slug='foundations', sort_order=1,
        )
        Unit.objects.create(
            module=logistics, title='Welcome', slug='welcome', sort_order=1,
        )
        first_lesson = Unit.objects.create(
            module=foundations, title='Build your first AI feature',
            slug='first-ai-feature', sort_order=1,
        )
        project_step = Unit.objects.create(
            module=foundations, title='Foundations project step',
            slug='foundations-project-step', kind='homework', sort_order=2,
        )
        series = EventSeries.objects.create(name='Cohort 4 sessions', slug='c4-sessions')
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4', external_key='cohort-4',
            start_date=timezone.localdate() - datetime.timedelta(days=3),
            end_date=timezone.localdate() + datetime.timedelta(days=60),
            event_series=series,
        )
        event = Event.objects.create(
            event_series=series, slug='cohort4-session', title='Cohort 4 session',
            status='upcoming', start_datetime=timezone.now() + datetime.timedelta(days=1),
            end_datetime=timezone.now() + datetime.timedelta(days=1, hours=1),
        )
        self.client.force_login(self.user)

        response = self.client.get('/courses/ai-buildcamp/home')

        self.assertIsNone(response.context['cohort'])
        self.assertEqual(response.context['focus_module'], foundations)
        self.assertEqual(response.context['recommended_unit'], first_lesson)
        self.assertEqual(
            [item['unit'] for item in response.context['focus_work_items']],
            [project_step],
        )
        self.assertContains(response, 'data-testid="course-home-no-cohort"')
        self.assertContains(response, 'data-testid="course-home-cohort-support"')
        self.assertContains(response, 'data-testid="course-home-get-started"')
        self.assertContains(response, 'data-testid="course-home-weekly-work"')
        self.assertEqual(response.context['live_session_schedule'], [])
        self.assertNotContains(response, cohort.name)
        self.assertNotContains(response, event.title)
        self.assertFalse(CohortEnrollment.objects.filter(user=self.user, cohort=cohort).exists())

    def test_python_home_uses_a_real_self_paced_cohort_enrollment(self):
        course = Course.objects.create(
            title='Python', slug='python', status='published', required_level=LEVEL_MAIN,
        )
        CourseAccess.objects.create(user=self.user, course=course, access_type='granted')
        lesson_module = Module.objects.create(
            course=course, title='Python foundations', slug='python-foundations',
        )
        lesson = Unit.objects.create(
            module=lesson_module, title='Python basics', slug='python-basics',
        )
        self.client.force_login(self.user)

        response = self.client.get('/courses/python/home')

        self.assertEqual(response.context['cohort'].mode, 'self_paced')
        self.assertEqual(response.context['recommended_unit'], lesson)
        self.assertEqual(
            CohortEnrollment.objects.filter(user=self.user, cohort__course=course).count(), 1,
        )
        self.assertFalse(Cohort.objects.filter(course=course, mode='cohort').exists())

    def test_detail_names_only_the_users_real_cohort_enrollment(self):
        python = Course.objects.create(
            title='Python', slug='python-detail', status='published', required_level=0,
        )
        Enrollment.objects.create(user=self.user, course=python)
        self.client.force_login(self.user)

        enrolled_detail = self.client.get('/courses/python-detail')

        self.assertContains(enrolled_detail, 'data-testid="course-detail-enrolled-cohort"')
        self.assertContains(enrolled_detail, 'Self-paced')
        self.assertEqual(enrolled_detail.context['enrolled_cohort'].mode, 'self_paced')

        scheduled_course = Course.objects.create(
            title='Scheduled course', slug='scheduled-detail',
            status='published', required_level=0,
        )
        scheduled_cohort = Cohort.objects.create(
            course=scheduled_course, name='Cohort 4', external_key='cohort-4',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 11, 22),
        )
        CohortEnrollment.objects.create(user=self.user, cohort=scheduled_cohort)
        Enrollment.objects.create(user=self.user, course=scheduled_course)

        scheduled_detail = self.client.get('/courses/scheduled-detail')

        self.assertContains(scheduled_detail, 'data-testid="course-detail-enrolled-cohort"')
        self.assertContains(scheduled_detail, 'Cohort 4')
        self.assertContains(scheduled_detail, 'datetime="2026-09-21"')
        self.assertContains(scheduled_detail, 'datetime="2026-11-22"')
        self.assertEqual(scheduled_detail.context['enrolled_cohort'], scheduled_cohort)

        buildcamp = Course.objects.create(
            title='AI Engineering Buildcamp', slug='ai-buildcamp-detail',
            status='published', required_level=0,
        )
        Cohort.objects.create(
            course=buildcamp, name='Cohort 4', external_key='cohort-4',
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + datetime.timedelta(days=60),
        )
        Enrollment.objects.create(user=self.user, course=buildcamp)

        unlinked_detail = self.client.get('/courses/ai-buildcamp-detail')

        self.assertNotContains(unlinked_detail, 'data-testid="course-detail-enrolled-cohort"')
        self.assertContains(unlinked_detail, "You're taking this course.")
        self.assertIsNone(unlinked_detail.context['enrolled_cohort'])

    def test_selected_active_cohort_drip_matches_openable_reader(self):
        self._cohort(key='future-first', start_days=30, end_days=93)
        active = self._cohort(key='active-selected', start_days=-3, end_days=60)
        self.client.force_login(self.user)
        response = self.client.get(
            f'/courses/{self.course.slug}/home?cohort={active.external_key}',
        )
        self.assertEqual(response.context['recommended_unit'], self.lesson1)
        lesson = self.client.get(
            f'{self.lesson1.get_absolute_url()}?cohort={active.external_key}',
        )
        self.assertNotContains(lesson, 'data-testid="drip-locked-card"')
        self.assertContains(lesson, self.lesson1.title)

    def test_calendar_week_eight_is_not_replaced_by_capstone_module_index(self):
        self.course.slug = 'ai-buildcamp'
        self.course.save(update_fields=['slug'])
        self.second.available_after_days = 7
        self.second.save(update_fields=['available_after_days'])
        for week in range(3, 8):
            module = Module.objects.create(
                course=self.course, title=f'Week {week}', slug=f'week-{week}',
                sort_order=week, available_after_days=(week - 1) * 7,
            )
            Unit.objects.create(module=module, title=f'Lesson {week}', slug=f'lesson-{week}')
        cohort = self._cohort(start_days=-50, end_days=13)
        model = build_course_home(self.course, self.user, cohort)
        self.assertEqual(model['current_week'], 8)

    def test_optional_children_do_not_add_one_query_each(self):
        parent = Module.objects.create(
            course=self.course, title='Further study', slug='further-study',
            sort_order=4,
        )
        with CaptureQueriesContext(connection) as before:
            build_course_home(self.course, self.user, None)
        for index in range(8):
            child = Module.objects.create(
                course=self.course, parent=parent, title=f'Optional {index}',
                slug=f'optional-{index}', sort_order=index, is_bonus=True,
            )
            Unit.objects.create(module=child, title=f'Lesson {index}', slug=f'lesson-{index}')
        with CaptureQueriesContext(connection) as after:
            model = build_course_home(self.course, self.user, None)
        self.assertEqual(len(model['optional_rows']), 9)
        self.assertLessEqual(len(after), len(before) + 2)

    def test_page_links_home_to_lesson_and_course_materials(self):
        self.client.force_login(self.user)
        response = self.client.get('/courses/course-home-test/home')
        self.assertContains(response, 'data-testid="course-home-open-lesson"')
        self.assertContains(response, f'href="{self.lesson1.get_absolute_url()}"')
        self.assertContains(response, f'href="{self.course.get_absolute_url()}"')
        self.assertContains(response, 'data-testid="course-home-position"')
        self.assertContains(response, 'Next uncompleted course material')
        lesson = self.client.get(self.lesson1.get_absolute_url())
        self.assertContains(lesson, 'data-testid="reader-course-home"')

    def test_individual_course_access_opens_home_without_membership(self):
        self.course.required_level = LEVEL_MAIN
        self.course.save(update_fields=['required_level'])
        CourseAccess.objects.create(user=self.user, course=self.course, access_type='granted')
        self.client.force_login(self.user)
        response = self.client.get('/courses/course-home-test/home')
        self.assertContains(response, self.course.discussion_url)
        self.assertContains(response, 'data-testid="course-home-open-lesson"')

    def test_help_and_optional_child_use_database_curriculum(self):
        self.course.discussion_url = ''
        self.course.save(update_fields=['discussion_url'])
        practice = Module.objects.create(
            course=self.course, title='Practice', slug='practice', sort_order=4,
        )
        core_child = Module.objects.create(
            course=self.course, parent=practice, title='Core examples',
            slug='core-examples', sort_order=1,
        )
        Unit.objects.create(module=core_child, title='One core example', slug='one-core-example')
        optional_child = Module.objects.create(
            course=self.course, parent=practice, title='Extra examples',
            slug='extra-examples', sort_order=4, is_bonus=True,
        )
        Unit.objects.create(
            module=optional_child, title='How the group communicates',
            slug='communication', sort_order=1,
        )
        Unit.objects.create(
            module=optional_child, title='Live coaching sessions',
            slug='office-hours', sort_order=2,
        )
        model = build_course_home(self.course, self.user, None)
        self.assertEqual(model['core_total'], 4)
        self.assertIn(optional_child, [row['module'] for row in model['optional_rows']])
        self.assertIn(
            ('How the group communicates',
             f'/courses/{self.course.slug}/{practice.slug}/{optional_child.slug}/communication'),
            model['help_links'],
        )
        self.assertIn(
            ('Live coaching sessions',
             f'/courses/{self.course.slug}/{practice.slug}/{optional_child.slug}/office-hours'),
            model['help_links'],
        )

    def test_empty_course_has_no_invented_action_or_module(self):
        Course.objects.create(
            title='Empty course', slug='empty-course-home', status='published',
        )
        self.client.force_login(self.user)
        response = self.client.get('/courses/empty-course-home/home')
        self.assertEqual(response.context['action'], 'empty')
        self.assertContains(response, 'Course materials have not been published yet')
        self.assertNotContains(response, 'Open lesson')

    def test_recommended_linked_session_uses_one_real_session_action(self):
        event_unit = Unit.objects.create(
            module=self.first, title='Session 1', slug='session-1', kind='event',
            session_position=1,
        )
        self._complete(self.lesson1, self.lesson2, self.lesson3)
        cohort = self._cohort()
        series = EventSeries.objects.create(name='Course sessions', slug='course-sessions')
        cohort.event_series = series
        cohort.save(update_fields=['event_series'])
        event = Event.objects.create(
            event_series=series, slug='first-session', title='Session 1 for this course',
            status='upcoming', start_datetime=timezone.now() + datetime.timedelta(days=1),
            end_datetime=timezone.now() + datetime.timedelta(days=1, hours=1),
            series_position=1,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            f'/courses/{self.course.slug}/home?cohort={cohort.external_key}',
        )

        self.assertEqual(response.context['recommended_unit'], event_unit)
        self.assertEqual(response.context['recommended_live_session']['title'], event.title)
        self.assertIsNone(response.context['next_live_session'])
        self.assertContains(response, 'data-testid="course-home-live-sessions"')
        self.assertContains(response, 'Your next live session is highlighted in the schedule below.')
        self.assertContains(response, 'data-featured="true"')
        self.assertContains(response, event.title, count=1)
        self.assertContains(response, f'href="{event.get_absolute_url()}"')

    def test_course_map_query_count_does_not_grow_per_module(self):
        with CaptureQueriesContext(connection) as base_queries:
            build_course_home(self.course, self.user, None)
        for index in range(4, 10):
            module = Module.objects.create(
                course=self.course, title=f'Module {index}', slug=f'module-{index}',
                sort_order=index,
            )
            Unit.objects.create(
                module=module, title=f'Lesson {index}', slug=f'lesson-{index}',
            )
        with CaptureQueriesContext(connection) as expanded_queries:
            build_course_home(self.course, self.user, None)
        self.assertLessEqual(len(expanded_queries), len(base_queries) + 2)
