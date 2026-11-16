"""Reader sidebar project rows match the syllabus project rows."""

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


def _project_rows_block(content):
    """The reader-project-rows list, including the current row's nested step nav."""
    start = content.index('data-testid="reader-project-rows"')
    depth = 1
    pos = start
    while depth:
        next_open = content.find('<ul', pos)
        next_close = content.index('</ul>', pos)
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 3
        else:
            depth -= 1
            pos = next_close + 5
    return content[start:pos]


class ReaderProjectRowsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='reader-rows@example.com', password='pw', email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Rows Course', slug='rows-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=2,
            reader_navigation_scope='module',
        )
        module = Module.objects.create(
            course=cls.course, title='Capstone', slug='capstone', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Lesson', slug='lesson', sort_order=1,
        )
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=cls.course, name='Rows cohort', external_key='rows',
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=90),
        )
        CohortEnrollment.objects.create(cohort=cohort, user=cls.user)
        cls.cohort = cohort
        now = timezone.now()
        cls.projects = []
        for number in (1, 2):
            project = CourseProject.objects.create(
                course=cls.course, cohort=cohort, module=module,
                slug='attempt-%d' % number,
                title='Capstone Project Attempt %d' % number,
                submission_due_at=now + timedelta(days=number),
                review_due_at=now + timedelta(days=number + 7),
            )
            cls.projects.append(project)

    def test_rows_use_syllabus_icon_full_title_and_no_heading(self):
        self.client.force_login(self.user)
        response = self.client.get('/courses/rows-course/projects/attempt-1/submit')
        content = response.content.decode()
        block = _project_rows_block(content)
        self.assertIn('data-lucide="folder-git-2"', block)
        self.assertNotIn('rocket', block)
        self.assertIn('Capstone Project Attempt 1', block)
        self.assertIn('Capstone Project Attempt 2', block)
        self.assertIn('data-homework-state="open">Open<', block)
        self.assertEqual(block.count('aria-current="page"'), 1)
        self.assertNotIn('Submit your project', content)

    def _rows(self, path):
        content = self.client.get(path).content.decode()
        return _project_rows_block(content).split('<li>')[1:]

    def _submit(self, project, user):
        return ProjectSubmission.objects.create(
            user=user, course=self.course, course_project=project,
            cohort=self.cohort, project_url='https://github.com/x/y',
        )

    def test_row_link_and_badge_follow_the_phase(self):
        attempt_1, attempt_2 = self.projects
        CourseProject.objects.filter(pk=attempt_1.pk).update(
            submission_due_at=timezone.now() - timedelta(days=1), state='PR',
        )
        self._submit(attempt_1, self.user)
        peer = User.objects.create_user(email='peer-rows@example.com', password='pw')
        PeerReview.objects.create(submission=self._submit(attempt_1, peer), reviewer=self.user)
        self._submit(attempt_2, self.user)
        self.client.force_login(self.user)
        first, second = self._rows('/courses/rows-course/capstone/lesson')
        self.assertIn('href="/courses/rows-course/projects/attempt-1/reviews"', first)
        self.assertIn('data-homework-state="review">Review 0/1<', first)
        self.assertIn('href="/courses/rows-course/projects/attempt-2/submit"', second)
        self.assertIn('data-homework-state="submitted">Submitted<', second)

    def test_missed_attempt_links_to_submit_page_and_reads_not_submitted(self):
        CourseProject.objects.filter(pk=self.projects[0].pk).update(
            submission_due_at=timezone.now() - timedelta(days=1), state='PR',
        )
        self.client.force_login(self.user)
        first = self._rows('/courses/rows-course/capstone/lesson')[0]
        self.assertIn('href="/courses/rows-course/projects/attempt-1/submit"', first)
        self.assertIn('data-homework-state="not_submitted">Not submitted<', first)


class AttemptSidebarStepsTest(TestCase):
    """On an attempt's pages its sidebar row expands like a homework unit."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='steps@example.com', password='pw', email_verified=True)
        cls.peers = [
            User.objects.create_user(email=f'steps-peer{n}@example.com', password='pw') for n in range(2)
        ]
        cls.course = Course.objects.create(
            title='Steps Course', slug='steps-course', status='published',
            required_level=0, peer_review_enabled=True, reader_navigation_scope='module',
        )
        module = Module.objects.create(course=cls.course, title='Capstone', slug='capstone', sort_order=1)
        Unit.objects.create(module=module, title='Lesson', slug='lesson', sort_order=1)
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Steps cohort', external_key='steps',
            start_date=today - timedelta(days=10), end_date=today + timedelta(days=90),
        )
        now = timezone.now()
        cls.project, cls.other = [
            CourseProject.objects.create(
                course=cls.course, cohort=cls.cohort, module=module,
                slug=f'attempt-{n}', title=f'Attempt {n}',
                state='PR' if n == 1 else 'CS',
                submission_due_at=now + timedelta(days=n * 3 - 4),
                review_due_at=now + timedelta(days=7 + n),
            )
            for n in (1, 2)
        ]
        cls.reviews = []
        for author in [cls.user, *cls.peers]:
            CohortEnrollment.objects.create(cohort=cls.cohort, user=author)
            submission = ProjectSubmission.objects.create(
                user=author, course=cls.course, cohort=cls.cohort, course_project=cls.project,
                project_url='https://github.com/x/y', status='in_review',
            )
            if author != cls.user:
                cls.reviews.append(PeerReview.objects.create(submission=submission, reviewer=cls.user))
        cls.reviews[0].is_complete = True
        cls.reviews[0].save(update_fields=['is_complete'])

    def setUp(self):
        self.client.force_login(self.user)

    def _steps(self, path):
        content = self.client.get(path).content.decode()
        start = content.index('data-testid="attempt-step-nav"')
        nav = content[start:content.index('</div>', start)]
        return content, nav.split('<li')[1:]

    def _pseudonym(self, user):
        from content.services.peer_pseudonym import peer_pseudonym
        return peer_pseudonym(user.pk, self.course.pk)

    def test_evaluation_page_lists_submit_then_reviews_by_pseudonym(self):
        base = '/courses/steps-course/projects/attempt-1'
        pending = self.reviews[1].submission_id
        content, steps = self._steps(f'{base}/reviews/{pending}')
        self.assertEqual([s.split('"')[1] for s in steps], ['submit', 'review', 'review'])
        self.assertIn(f'href="{base}/submit"', steps[0])
        self.assertIn('data-attempt-step-done="true"', steps[0])
        self.assertIn(f'Review 1: {self._pseudonym(self.peers[0])}', steps[1])
        self.assertIn('data-attempt-step-done="true"', steps[1])
        self.assertIn(f'Review 2: {self._pseudonym(self.peers[1])}', steps[2])
        self.assertIn('data-lucide="circle"', steps[2])
        self.assertNotIn('user-check', steps[2])
        self.assertIn(f'href="{base}/reviews/{pending}"', steps[2])
        self.assertIn('aria-current="step"', steps[2])
        self.assertNotIn('aria-current="step"', steps[1])
        self.assertNotIn('steps-peer', content)
        self.assertEqual(content.count('data-testid="attempt-step-nav"'), 1)
        self.assertIn('data-reader-breadcrumb', content)
        self.assertNotIn('data-testid="peer-review-back-link"', content)

    def test_feedback_item_waits_for_staff_to_complete_the_project(self):
        PeerReview.objects.filter(reviewer=self.user).update(is_complete=True)
        _content, steps = self._steps('/courses/steps-course/projects/attempt-1/reviews')
        self.assertEqual([s.split('"')[1] for s in steps], ['submit', 'review', 'review'])
        sidebar = self.client.get('/courses/steps-course/projects/attempt-1/reviews').content.decode()
        self.assertIn('data-homework-state="review_completed">Review completed<', sidebar)

    def test_feedback_item_appears_once_project_is_completed(self):
        PeerReview.objects.filter(reviewer=self.user).update(is_complete=True)
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        _content, steps = self._steps('/courses/steps-course/projects/attempt-1/reviews')
        self.assertEqual([s.split('"')[1] for s in steps], ['submit', 'review', 'review', 'feedback'])
        self.assertIn('aria-current="step"', steps[3])

    def test_submit_page_has_no_phase_pills(self):
        content, steps = self._steps('/courses/steps-course/projects/attempt-1/submit')
        self.assertIn('aria-current="step"', steps[0])
        self.assertNotIn('data-testid="attempt-phases"', content)

    def test_sidebar_badge_shows_review_progress(self):
        content = self.client.get('/courses/steps-course/projects/attempt-1/reviews').content.decode()
        self.assertIn('data-homework-state="review">Review 1/2<', content)
