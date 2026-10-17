"""Self-paced course Home, tabs, and module page: no calendar anywhere.

A self-paced learner has no live sessions, no due dates, and so no
"Shown in your timezone" notice. Homework and projects stay listed without
dates. A dated cohort's learner keeps all three.
"""

from html.parser import HTMLParser

from django.utils import timezone

from accounts.models import User
from content.models import Cohort, CohortEnrollment, UserCourseProgress
from content.models.homework import Homework
from content.models.peer_review import CourseProject, PeerReview, ProjectSubmission
from content.tests.test_course_home_current_module import CurrentModuleFixture

TIMEZONE_NOTE = 'Shown in your timezone.'
NO_FIXED_SUBMISSION_DEADLINE = 'No fixed submission deadline'
OPEN_REVIEW_WINDOW = 'Reviews remain open until completed'


class _TestIdTextParser(HTMLParser):
    def __init__(self, testid):
        super().__init__()
        self.testid = testid
        self.depth = 0
        self.chunks = []
        self.texts = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.depth += 1
        elif dict(attrs).get('data-testid') == self.testid:
            self.depth = 1
            self.chunks = []

    def handle_data(self, data):
        if self.depth:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if not self.depth:
            return
        self.depth -= 1
        if not self.depth:
            self.texts.append(' '.join(''.join(self.chunks).split()))


def _texts_for_testid(response, testid):
    parser = _TestIdTextParser(testid)
    parser.feed(response.content.decode())
    return parser.texts


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
        CourseProject.objects.create(
            course=cls.course, cohort=cls.self_paced, module=cls.second,
            slug='retrieval-project',
            title='Retrieval project',
            submission_due_at=None,
            review_due_at=None,
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
        self.assertContains(response, NO_FIXED_SUBMISSION_DEADLINE)

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
                if tab != 'homework':
                    self.assertContains(response, NO_FIXED_SUBMISSION_DEADLINE)

    def _assert_submitted_project_copy(self, response, path):
        self.assertContains(response, NO_FIXED_SUBMISSION_DEADLINE)
        if path == 'home':
            project_row = next(
                row for row in response.context['current_module']['deliverables']
                if row['kind'] == 'Project'
            )
            detail = f'{NO_FIXED_SUBMISSION_DEADLINE} · {OPEN_REVIEW_WINDOW}'
            self.assertEqual(project_row['detail'], detail)
            self.assertIn(
                detail,
                _texts_for_testid(response, 'course-home-deadline-detail'),
            )
        elif path != self.second.slug:
            self.assertContains(response, OPEN_REVIEW_WINDOW)

    def test_submitted_project_overviews_explain_open_review_window(self):
        self.course.peer_review_enabled = True
        self.course.save(update_fields=['peer_review_enabled'])
        project = CourseProject.objects.get(
            course=self.course, cohort=self.self_paced,
        )
        submission = ProjectSubmission.objects.create(
            user=self.learner, course=self.course, cohort=self.self_paced,
            course_project=project, project_url='https://example.com/project',
        )
        PeerReview.objects.create(submission=submission, reviewer=self.learner)

        for path in ('home', 'home/projects', 'home/syllabus', self.second.slug):
            with self.subTest(path=path):
                response = self._get(f'/courses/{self.course.slug}/{path}')
                self._assert_submitted_project_copy(response, path)

    def test_live_sessions_url_redirects_to_home(self):
        response = self._get(f'/courses/{self.course.slug}/home/sessions')

        self.assertRedirects(response, f'/courses/{self.course.slug}/home')

    def test_module_page_has_no_sessions_dates_or_timezone_note(self):
        response = self._get(f'/courses/{self.course.slug}/{self.second.slug}')

        self._assert_no_calendar(response)
        self.assertEqual(response.context['module_session_rows'], [])
        self.assertContains(response, 'data-testid="module-work-status"')
        self.assertContains(response, 'Self-paced retrieval homework')
        self.assertContains(response, NO_FIXED_SUBMISSION_DEADLINE)

    def test_dated_cohort_learner_keeps_sessions_due_dates_and_timezone_note(self):
        self.client.force_login(self.user)

        response = self.client.get(self.home_url)

        self.assertContains(response, f'/courses/{self.course.slug}/home/sessions')
        self.assertContains(response, 'data-testid="course-home-next-session"')
        self.assertContains(response, 'Due <time')
        # Issue #1795: an empty saved preference renders the explicit
        # fallback zone; the client localizes it in place on first visit.
        self.assertContains(
            response,
            'Event times are shown in '
            '<span data-schedule-zone-label>Europe/Berlin</span>.',
        )
