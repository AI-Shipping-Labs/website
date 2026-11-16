"""Course home project rows follow the learner's attempt phase (as CMP)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Module,
    PeerReview,
    ProjectSubmission,
    Unit,
)
from content.models.peer_review import CourseProject


class CourseHomeProjectPhaseRowTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='home-phase@example.com', password='pw', email_verified=True,
        )
        peer = User.objects.create_user(email='home-phase-peer@example.com', password='pw')
        cls.course = Course.objects.create(
            title='Phase Course', slug='phase-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=1,
        )
        module = Module.objects.create(
            course=cls.course, title='Capstone', slug='capstone', sort_order=1,
        )
        Unit.objects.create(module=module, title='Lesson', slug='lesson', sort_order=1)
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=cls.course, name='Phase cohort', external_key='phase',
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=30),
        )
        CohortEnrollment.objects.create(cohort=cohort, user=cls.user)
        now = timezone.now()
        cls.review_due = now + timedelta(days=5)
        project = CourseProject.objects.create(
            course=cls.course, cohort=cohort, module=module,
            slug='attempt-1', title='Capstone Attempt 1', state='PR',
            submission_due_at=now - timedelta(days=1), review_due_at=cls.review_due,
        )
        for author in (cls.user, peer):
            ProjectSubmission.objects.create(
                user=author, course=cls.course, course_project=project,
                cohort=cohort, project_url='https://github.com/x/y',
            )
        PeerReview.objects.create(
            submission=ProjectSubmission.objects.get(user=peer), reviewer=cls.user,
        )

    def _rows(self, path='/courses/phase-course/home/projects'):
        content = self.client.get(path).content.decode()
        return content, content.split('data-testid="course-home-deadline-row"')[1:]

    def test_attempt_in_review_links_to_reviews_with_review_badge_and_review_deadline(self):
        self.client.force_login(self.user)
        content, rows = self._rows()
        row = next(r for r in rows if 'Capstone Attempt 1' in r)
        self.assertIn('href="/courses/phase-course/projects/attempt-1/reviews"', row)
        self.assertIn(f'datetime="{self.review_due.isoformat()}"', row)
        self.assertIn('Review 0/1', row)
        self.assertNotIn('Completed work', content)

    def test_projects_tab_lists_every_attempt_in_every_state(self):
        """CMP lists every project, whatever its state or whether it was submitted."""
        self.client.force_login(self.user)
        now = timezone.now()
        module = Module.objects.get(slug='capstone')
        cohort = Cohort.objects.get(external_key='phase')
        second = CourseProject.objects.create(
            course=self.course, cohort=cohort, module=module, slug='attempt-2',
            title='Capstone Attempt 2', submission_due_at=now + timedelta(days=20),
            review_due_at=now + timedelta(days=27),
        )
        cases = (
            ('CS', 'Open', None),
            ('CL', 'Closed', None),
            ('PR', 'Not submitted', None),
            ('CO', 'Not submitted', None),
            ('CS', 'Submitted', 'submitted'),
            ('PR', 'Review', 'submitted'),
            ('CO', 'Passed', 'review_complete'),
            ('CO', '0 points', 'review_complete'),
        )
        for state, badge, status in cases:
            with self.subTest(state=state, submitted=status):
                ProjectSubmission.objects.filter(course_project=second).delete()
                CourseProject.objects.filter(pk=second.pk).update(state=state)
                if status:
                    ProjectSubmission.objects.create(
                        user=self.user, course=self.course, course_project=second,
                        cohort=cohort, project_url='https://github.com/x/z', status=status,
                    )
                content, rows = self._rows()
                titles = [t for t in ('Capstone Attempt 1', 'Capstone Attempt 2') if any(t in r for r in rows)]
                self.assertEqual(titles, ['Capstone Attempt 1', 'Capstone Attempt 2'])
                self.assertNotIn('Completed work', content)

    def test_homework_tab_has_no_collapsed_completed_section(self):
        self.client.force_login(self.user)
        content = self.client.get('/courses/phase-course/home/homework').content.decode()
        self.assertNotIn('Completed work', content)
