"""Self-paced course Home, tabs, and module page: no calendar anywhere.

A self-paced learner has no live sessions, no due dates, and so no
"Shown in your timezone" notice. Homework and projects stay listed without
dates. A dated cohort's learner keeps all three.
"""

import datetime

from django.utils import timezone

from accounts.models import User
from content.models import Cohort, CohortEnrollment, UserCourseProgress
from content.models.homework import Homework
from content.models.peer_review import CourseProject
from content.tests.test_course_home_current_module import CurrentModuleFixture

TIMEZONE_NOTE = 'Shown in your timezone.'


class SelfPacedCourseHomeTests(CurrentModuleFixture):
    """``CurrentModuleFixture`` plus a self-paced cohort and its learner."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.learner = User.objects.create_user(
            email='self-paced-learner@example.com', password='testpass',
            email_verified=True,
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', external_key='self-paced',
            mode='self_paced',
        )
        CohortEnrollment.objects.create(cohort=cls.self_paced, user=cls.learner)
        # Foundations is done, so the retrieval module is the learner's next.
        UserCourseProgress.objects.create(
            user=cls.learner, unit=cls.earlier_lesson, completed_at=timezone.now(),
        )
        Homework.objects.create(
            cohort=cls.self_paced, content_id=cls.homework_unit.content_id,
            slug='retrieval-homework', title='Self-paced retrieval homework',
        )
        now = timezone.now()
        CourseProject.objects.create(
            course=cls.course, module=cls.second, slug='retrieval-project',
            title='Retrieval project',
            submission_due_at=now + datetime.timedelta(days=10),
            review_due_at=now + datetime.timedelta(days=17),
        )

    def setUp(self):
        # Dated sessions in the course's live cohort: the session resolver
        # falls back to them, so they would leak into a self-paced view.
        self._session(2, days=-2, title='Past retrieval session')
        self._session(3, days=3, title='Upcoming agents session')

    def _get(self, path):
        self.client.force_login(self.learner)
        return self.client.get(path)

    def _assert_no_calendar(self, response):
        self.assertNotContains(response, TIMEZONE_NOTE)
        self.assertNotContains(response, f'/courses/{self.course.slug}/home/sessions')
        self.assertNotContains(response, 'Past retrieval session')
        self.assertNotContains(response, 'Upcoming agents session')
        self.assertNotContains(response, 'Due <time')
        self.assertNotContains(response, 'Submit by')
        self.assertNotContains(response, 'Deadline to be announced')

    def test_home_has_no_sessions_due_dates_or_timezone_note_but_keeps_work(self):
        response = self._get(f'/courses/{self.course.slug}/home')

        self._assert_no_calendar(response)
        self.assertEqual(response.context['cohort'], self.self_paced)
        self.assertIsNone(response.context['home_next_session'])
        self.assertIsNone(response.context['due_next'])
        self.assertEqual(response.context['current_module']['sessions'], [])
        self.assertEqual(response.context['current_module']['module'], self.second)
        self.assertNotContains(response, 'data-testid="course-home-module-sessions"')
        self.assertContains(response, 'data-testid="course-home-deliverables"')
        self.assertContains(response, 'Self-paced retrieval homework')

    def test_homework_projects_and_syllabus_tabs_list_work_without_dates(self):
        for tab, title in (
            ('homework', 'Self-paced retrieval homework'),
            ('projects', 'Retrieval project'),
            ('syllabus', 'Retrieval project'),
        ):
            with self.subTest(tab=tab):
                response = self._get(f'/courses/{self.course.slug}/home/{tab}')
                self._assert_no_calendar(response)
                self.assertContains(response, title)

    def test_live_sessions_url_redirects_to_home(self):
        response = self._get(f'/courses/{self.course.slug}/home/sessions')

        self.assertRedirects(response, f'/courses/{self.course.slug}/home')

    def test_module_page_has_no_sessions_dates_or_timezone_note(self):
        response = self._get(f'/courses/{self.course.slug}/{self.second.slug}')

        self._assert_no_calendar(response)
        self.assertEqual(response.context['module_session_rows'], [])
        self.assertContains(response, 'data-testid="module-work-status"')
        self.assertContains(response, 'Self-paced retrieval homework')

    def test_dated_cohort_learner_keeps_sessions_due_dates_and_timezone_note(self):
        self.client.force_login(self.user)

        response = self.client.get(self.home_url)

        self.assertContains(response, f'/courses/{self.course.slug}/home/sessions')
        self.assertContains(response, 'data-testid="course-home-next-session"')
        self.assertContains(response, 'Due <time')
        self.assertContains(response, TIMEZONE_NOTE)
