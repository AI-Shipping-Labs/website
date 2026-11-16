"""Course Home's "Complete your peer reviews" prompt (peer_review_prompt)."""

import re
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from content.models import Cohort, CohortEnrollment, Course, Module, PeerReview, ProjectSubmission, Unit
from content.models.peer_review import CourseProject
from content.services.course_commitments import build_course_commitments

HOME = '/courses/prompt-course/home'


class PeerReviewPromptTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='prompt@example.com', password='pw', email_verified=True)
        cls.peers = [User.objects.create_user(email=f'prompt-peer{n}@example.com', password='pw') for n in range(3)]
        cls.course = Course.objects.create(
            title='Prompt course', slug='prompt-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=3,
        )
        module = Module.objects.create(course=cls.course, title='Capstone', slug='capstone', sort_order=1)
        Unit.objects.create(module=module, title='Lesson', slug='lesson', sort_order=1)
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Prompt cohort', external_key='prompt',
            start_date=today - timedelta(days=20), end_date=today + timedelta(days=20),
        )
        now = timezone.now()
        cls.project = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, module=module, slug='attempt-1', title='Attempt 1', state='PR',
            submission_due_at=now - timedelta(days=1), review_due_at=now + timedelta(days=6),
        )
        for user in [cls.user, *cls.peers]:
            CohortEnrollment.objects.create(cohort=cls.cohort, user=user)
            ProjectSubmission.objects.create(
                user=user, course=cls.course, cohort=cls.cohort, course_project=cls.project,
                project_url='https://github.com/x/y', status='in_review',
            )

    def setUp(self):
        self.client.force_login(self.user)

    def _assign(self, done):
        reviews = []
        for index, peer in enumerate(self.peers):
            reviews.append(PeerReview.objects.create(
                submission=ProjectSubmission.objects.get(user=peer), reviewer=self.user,
                is_complete=index < done,
            ))
        return reviews

    def _card(self, path=HOME):
        content = self.client.get(path).content.decode()
        match = re.search(r'<section[^>]*data-testid="peer-review-prompt"[^>]*>.*?</section>', content, flags=re.S)
        return content, match.group(0) if match else ''

    def test_pending_reviews_show_progress_deadline_and_next_evaluation(self):
        reviews = self._assign(done=1)
        content, card = self._card()
        self.assertIn('data-state="pending"', card)
        self.assertIn('Complete your peer reviews', card)
        self.assertIn('Required for your certificate.', card)
        self.assertIn('data-testid="peer-review-prompt-count">1 of 3 complete</p>', card)
        self.assertIn('aria-valuenow="1"', card)
        self.assertIn('data-testid="peer-review-prompt-deadline"', card)
        next_url = f'/courses/prompt-course/projects/attempt-1/reviews/{reviews[1].submission_id}'
        self.assertRegex(card, r'href="' + re.escape(next_url) + r'"[^>]*data-testid="peer-review-prompt-action">Continue reviews')
        self.assertGreater(content.index('data-testid="peer-review-prompt"'), content.index('data-cb-syllabus'))

    def test_projects_tab_shows_the_same_card(self):
        self._assign(done=0)
        _content, card = self._card(f'{HOME}/projects')
        self.assertIn('data-testid="peer-review-prompt-count">0 of 3 complete</p>', card)

    def test_all_reviews_done_shows_no_prompt(self):
        self._assign(done=3)
        _content, card = self._card()
        self.assertEqual(card, '')

    def test_submitted_without_assignment_waits_for_review_group(self):
        _content, card = self._card()
        self.assertIn('data-state="waiting"', card)
        self.assertIn('Waiting for your review group', card)

    def test_no_submission_shows_no_prompt(self):
        ProjectSubmission.objects.filter(user=self.user).delete()
        _content, card = self._card()
        self.assertEqual(card, '')

    def test_pending_reviews_row_links_to_next_evaluation(self):
        reviews = self._assign(done=2)
        rows = build_course_commitments(self.course, self.user, self.cohort)['open_assignments']
        review_row = next(row for row in rows if row['kind'] == 'Peer reviews')
        self.assertEqual(review_row['url'], f'/courses/prompt-course/projects/attempt-1/reviews/{reviews[2].submission_id}')
        self.assertEqual((review_row['status'], review_row['status_tone']), ('2 of 3 complete', 'warning'))
