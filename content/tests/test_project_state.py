"""Staff-driven project state (CMP's Project.state): service, command, Studio."""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, CourseProject, PeerReview, ProjectSubmission
from content.services.project_state import ProjectStateError, parse_state, set_project_state

User = get_user_model()


class ProjectStateTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email='state-staff@test.com', password='pw', is_staff=True)
        cls.member = User.objects.create_user(email='state-member@test.com', password='pw')
        cls.learner = User.objects.create_user(email='state-learner@test.com', password='pw')
        cls.peers = [User.objects.create_user(email=f'state-peer{n}@test.com', password='pw') for n in range(2)]
        cls.course = Course.objects.create(
            title='State Course', slug='state-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=2,
        )
        now = timezone.now()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='State cohort', external_key='state',
            start_date=timezone.localdate() - timedelta(days=20),
            end_date=timezone.localdate() + timedelta(days=20),
        )
        cls.project = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, slug='attempt-1', title='Attempt 1', state='PR',
            submission_due_at=now - timedelta(days=2), review_due_at=now + timedelta(days=5),
            evaluation_criteria=[{
                'id': 'readme', 'title': 'README', 'description': '', 'type': 'scale',
                'options': [{'points': 0, 'text': 'None'}, {'points': 2, 'text': 'Good'}],
            }],
        )
        for user in (cls.learner, *cls.peers):
            CohortEnrollment.objects.create(cohort=cls.cohort, user=user)
        cls.own = cls._submit(cls.learner)
        for peer in cls.peers:
            PeerReview.objects.create(
                submission=cls._submit(peer), reviewer=cls.learner,
                is_complete=True, criteria_scores={'readme': 1},
            )

    @classmethod
    def _submit(cls, user):
        return ProjectSubmission.objects.create(
            user=user, course=cls.course, cohort=cls.cohort, course_project=cls.project,
            project_url='https://github.com/x/y', status='in_review',
        )


class ProjectStateServiceTest(ProjectStateTestBase):
    def test_parse_accepts_codes_names_and_labels(self):
        for text in ('PR', 'peer_reviewing', 'Peer reviewing', 'peer-reviewing'):
            self.assertEqual(parse_state(text), 'PR')
        with self.assertRaises(ProjectStateError):
            parse_state('nonsense')

    def test_only_a_peer_reviewing_project_can_be_completed(self):
        set_project_state(self.project, 'CS')
        with self.assertRaises(ProjectStateError):
            set_project_state(self.project, 'CO')
        set_project_state(self.project, 'PR')
        set_project_state(self.project, 'CO', actor=self.staff)
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'CO')

    def test_self_paced_attempts_have_no_state(self):
        cohort = Cohort.objects.create(
            course=self.course, name='SP', external_key='sp', mode='self_paced',
            start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=5),
        )
        project = CourseProject.objects.create(course=self.course, cohort=cohort, slug='sp', title='SP')
        with self.assertRaises(ProjectStateError):
            set_project_state(project, 'PR')


class LearnerSeesResultsOnlyWhenCompletedTest(ProjectStateTestBase):
    URL = '/courses/state-course/projects/attempt-1/reviews'

    def setUp(self):
        self.client.force_login(self.learner)

    def test_all_reviews_done_but_not_completed_shows_review_completed_and_no_results(self):
        html = self.client.get(self.URL).content.decode()
        self.assertIn('Review completed', html)
        self.assertNotIn('data-testid="peer-results"', html)
        self.assertNotIn('data-testid="peer-project-outcome"', html)

    def test_completed_shows_feedback_with_results_and_reopen_hides_them(self):
        set_project_state(self.project, 'CO')
        html = self.client.get(self.URL).content.decode()
        self.assertIn('data-testid="peer-results"', html)
        self.assertIn('data-testid="project-title">Feedback</h1>', html)
        set_project_state(self.project, 'PR')
        self.assertNotIn('data-testid="peer-results"', self.client.get(self.URL).content.decode())

    def test_review_form_is_closed_once_completed(self):
        set_project_state(self.project, 'CO')
        review = PeerReview.objects.filter(reviewer=self.learner).first()
        response = self.client.post(
            f'{self.URL}/{review.submission_id}', {'criterion-readme': '1', 'feedback': 'x'},
        )
        self.assertEqual(response.status_code, 403)


class StudioProjectStateTest(ProjectStateTestBase):
    def _url(self):
        return f'/studio/courses/{self.course.pk}/peer-reviews/projects/{self.project.pk}/state'

    def test_staff_closes_and_reopens_peer_review(self):
        self.client.force_login(self.staff)
        page = self.client.get(f'/studio/courses/{self.course.pk}/peer-reviews')
        self.assertContains(page, 'data-testid="project-close-peer-review"')
        self.client.post(self._url(), {'state': 'CO'})
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'CO')
        page = self.client.get(f'/studio/courses/{self.course.pk}/peer-reviews')
        self.assertContains(page, 'data-testid="project-reopen"')
        self.client.post(self._url(), {'state': 'PR'})
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'PR')

    def test_non_staff_and_anonymous_cannot_change_state(self):
        self.client.force_login(self.member)
        self.assertIn(self.client.post(self._url(), {'state': 'CO'}).status_code, (302, 403, 404))
        self.client.logout()
        self.assertIn(self.client.post(self._url(), {'state': 'CO'}).status_code, (302, 403, 404))
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'PR')

    def test_get_is_not_allowed(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self._url()).status_code, 405)

    def test_form_review_batches_moves_collecting_project_to_peer_reviewing(self):
        CourseProject.objects.filter(pk=self.project.pk).update(state='CS')
        PeerReview.objects.all().delete()
        ProjectSubmission.objects.update(status='submitted')
        self.client.force_login(self.staff)
        self.client.post(f'/studio/courses/{self.course.pk}/peer-reviews/form-batch')
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'PR')
        self.assertTrue(PeerReview.objects.exists())


class ProjectStateCommandTest(ProjectStateTestBase):
    def test_close_peer_review_completes_the_project(self):
        out = StringIO()
        call_command('close_peer_review', 'state-course', 'attempt-1', stdout=out)
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'CO')
        self.assertIn('peer review closed', out.getvalue())

    def test_set_project_state_reopens(self):
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        call_command('set_project_state', 'state-course', 'attempt-1', 'peer_reviewing', stdout=StringIO())
        self.project.refresh_from_db()
        self.assertEqual(self.project.state, 'PR')

    def test_unknown_project_or_state_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command('close_peer_review', 'state-course', 'nope')
        with self.assertRaises(CommandError):
            call_command('set_project_state', 'state-course', 'attempt-1', 'bogus')
