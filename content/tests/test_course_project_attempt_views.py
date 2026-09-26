"""Learner flows for separate dated course project attempts."""

from datetime import date, timedelta
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course, Module, PeerReview, ProjectSubmission, Unit
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject


class _SyllabusProjectNestingParser(HTMLParser):
    """Record project rows' ancestry in week/topic disclosure elements."""

    def __init__(self):
        super().__init__()
        self.detail_stack = []
        self.project_rows = []
        self.homework_rows = []
        self.active_unit_rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            classes = set(attrs.get('class', '').split())
            if 'syllabus-week' in classes:
                self.detail_stack.append('week')
            elif 'syllabus-topic' in classes:
                self.detail_stack.append('topic')
            else:
                self.detail_stack.append(None)
        if attrs.get('data-testid') == 'syllabus-project-attempt':
            self.project_rows.append({
                'week_depth': self.detail_stack.count('week'),
                'topic_depth': self.detail_stack.count('topic'),
            })
        if tag == 'a' and 'data-syllabus-unit-row' in attrs:
            row = {
                'week_depth': self.detail_stack.count('week'),
                'topic_depth': self.detail_stack.count('topic'),
            }
            self.active_unit_rows.append(row)
        if attrs.get('data-testid') == 'syllabus-homework-icon' and self.active_unit_rows:
            self.homework_rows.append(self.active_unit_rows[-1])

    def handle_endtag(self, tag):
        if tag == 'a' and self.active_unit_rows:
            self.active_unit_rows.pop()
        if tag == 'details' and self.detail_stack:
            self.detail_stack.pop()


class CourseProjectAttemptViewsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email='learner@example.com', password='pw')
        cls.course = Course.objects.create(
            title='Buildcamp', slug='ai-buildcamp', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=2,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Capstone Project', slug='capstone',
            sort_order=7,
        )
        cls.topic = Module.objects.create(
            course=cls.course, parent=cls.module, title='Capstone Overview',
            slug='capstone-overview', sort_order=1,
        )
        Unit.objects.create(
            module=cls.topic, title='Project overview', slug='project-overview',
            sort_order=1,
        )
        cls.homework = Module.objects.create(
            course=cls.course, parent=cls.module, title='Homework',
            slug='homework', sort_order=2,
        )
        Unit.objects.create(
            module=cls.homework, title='Plan your project', slug='plan-your-project',
            sort_order=1, kind='homework',
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 1', external_key='1',
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=90),
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.user)
        now = timezone.now()
        cls.first = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, module=cls.module,
            slug='first', title='First attempt',
            submission_due_at=now + timedelta(days=1),
            review_due_at=now + timedelta(days=8),
        )
        cls.second = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, module=cls.module,
            slug='second', title='Second attempt',
            submission_due_at=now + timedelta(days=15),
            review_due_at=now + timedelta(days=22),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_course_curriculum_lists_both_attempts_with_distinct_links(self):
        response = self.client.get('/courses/ai-buildcamp')
        self.assertContains(response, 'First attempt')
        self.assertContains(response, 'Second attempt')
        self.assertContains(response, '/courses/ai-buildcamp/projects/first/submit')
        self.assertContains(response, '/courses/ai-buildcamp/projects/second/submit')
        self.assertNotContains(response, 'Project Attempts')
        self.assertNotContains(response, 'data-testid="syllabus-project-group"')
        parser = _SyllabusProjectNestingParser()
        parser.feed(response.content.decode())
        self.assertEqual(
            parser.homework_rows,
            [{'week_depth': 1, 'topic_depth': 0}],
        )
        self.assertEqual(
            parser.project_rows,
            [{'week_depth': 1, 'topic_depth': 0}] * 2,
        )

    def test_review_links_appear_only_after_submission_of_that_attempt(self):
        response = self.client.get('/courses/ai-buildcamp')
        self.assertNotContains(response, '/courses/ai-buildcamp/projects/first/reviews')
        self.assertNotContains(response, '/courses/ai-buildcamp/projects/second/reviews')
        self.assertNotContains(response, 'Reviews by')

        self.client.post(
            '/courses/ai-buildcamp/projects/first/submit',
            {'project_url': 'https://example.com/first'},
        )
        response = self.client.get('/courses/ai-buildcamp')
        self.assertContains(response, '/courses/ai-buildcamp/projects/first/reviews')
        self.assertNotContains(response, '/courses/ai-buildcamp/projects/second/reviews')
        self.assertContains(response, 'Reviews by', count=1)

    def test_capstone_module_page_shows_its_nested_topics(self):
        response = self.client.get(
            '/courses/ai-buildcamp/capstone',
        )
        self.assertContains(response, 'Capstone Overview')
        self.assertContains(response, 'data-testid="module-submodule-link"', count=1)
        self.assertNotContains(response, 'data-testid="module-lesson-list"')

    def test_submissions_are_isolated_per_attempt(self):
        for slug in ('first', 'second'):
            response = self.client.post(
                f'/courses/ai-buildcamp/projects/{slug}/submit',
                {'project_url': f'https://example.com/{slug}'},
            )
            self.assertContains(response, 'Your project has been submitted.')
        self.assertEqual(ProjectSubmission.objects.filter(user=self.user).count(), 2)
        response = self.client.get('/courses/ai-buildcamp/projects/second/reviews')
        self.assertContains(response, 'https://example.com/second')
        self.assertNotContains(response, 'https://example.com/first')

    def test_submission_deadline_blocks_late_post(self):
        self.first.submission_due_at = timezone.now() - timedelta(minutes=1)
        self.first.save(update_fields=['submission_due_at'])
        response = self.client.post(
            '/courses/ai-buildcamp/projects/first/submit',
            {'project_url': 'https://example.com/late'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectSubmission.objects.filter(course_project=self.first).exists())
        response = self.client.get('/courses/ai-buildcamp/projects/second/submit')
        self.assertContains(response, 'Submit project')

    def test_legacy_submit_link_points_to_attempt_picker(self):
        response = self.client.get('/courses/ai-buildcamp/submit')
        self.assertRedirects(response, '/courses/ai-buildcamp#syllabus', fetch_redirect_response=False)

    def test_review_cannot_be_submitted_after_attempt_deadline(self):
        other = get_user_model().objects.create_user(email='other@example.com', password='pw')
        submission = ProjectSubmission.objects.create(
            user=other, course=self.course, course_project=self.first,
            project_url='https://example.com/other',
        )
        review = PeerReview.objects.create(submission=submission, reviewer=self.user)
        self.first.review_due_at = timezone.now() - timedelta(minutes=1)
        self.first.save(update_fields=['review_due_at'])
        response = self.client.post(
            f'/courses/ai-buildcamp/projects/first/reviews/{submission.pk}',
            {'feedback': 'Looks good'},
        )
        self.assertEqual(response.status_code, 403)
        review.refresh_from_db()
        self.assertFalse(review.is_complete)

    def test_cohort_attempt_is_only_available_to_its_members(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Cohort 4', external_key='4',
            start_date=date(2026, 9, 21), end_date=date(2026, 11, 22),
        )
        self.first.cohort = cohort
        self.first.save(update_fields=['cohort'])
        response = self.client.get('/courses/ai-buildcamp')
        self.assertNotContains(response, '/courses/ai-buildcamp/projects/first/submit')
        self.assertEqual(self.client.get('/courses/ai-buildcamp/projects/first/submit').status_code, 404)
        response = self.client.get('/courses/ai-buildcamp?cohort=4')
        self.assertNotContains(response, '/courses/ai-buildcamp/projects/first/submit')
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/ai-buildcamp?cohort=4')
        self.assertContains(response, '/courses/ai-buildcamp/projects/first/submit')
