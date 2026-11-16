"""Peer review pages must not reveal other learners' identities."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Course, PeerReview, ProjectSubmission

User = get_user_model()

REVIEWER_EMAIL = 'zed.reviewer.secret@example.org'
AUTHOR_EMAIL = 'quinn.author.secret@example.org'
REVIEWER_USERNAME = 'zedreviewerhandle'
AUTHOR_USERNAME = 'quinnauthorhandle'


class PeerReviewIdentityPrivacyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Privacy Course', slug='privacy-course', status='published',
            peer_review_enabled=True, peer_review_count=1,
        )
        cls.reviewer = User.objects.create_user(
            email=REVIEWER_EMAIL, password='pw', first_name=REVIEWER_USERNAME,
            email_verified=True,
        )
        cls.author = User.objects.create_user(
            email=AUTHOR_EMAIL, password='pw', first_name=AUTHOR_USERNAME,
            email_verified=True,
        )
        cls.author_submission = ProjectSubmission.objects.create(
            user=cls.author, course=cls.course,
            project_url='https://github.com/author/project', status='in_review',
        )
        cls.reviewer_submission = ProjectSubmission.objects.create(
            user=cls.reviewer, course=cls.course,
            project_url='https://github.com/reviewer/project', status='review_complete',
        )
        cls.assigned = PeerReview.objects.create(
            submission=cls.author_submission, reviewer=cls.reviewer,
        )
        cls.received = PeerReview.objects.create(
            submission=cls.reviewer_submission, reviewer=cls.author,
            score=4, feedback='Nice work', is_complete=True,
        )

    def _assert_no_identity(self, response):
        for secret in (REVIEWER_EMAIL, AUTHOR_EMAIL, REVIEWER_USERNAME, AUTHOR_USERNAME):
            self.assertNotContains(response, secret)

    def test_dashboard_hides_identities_for_reviewer_and_author(self):
        for user in (self.reviewer, self.author):
            self.client.force_login(user)
            response = self.client.get('/courses/privacy-course/reviews')
            self.assertContains(response, 'Submission 1')
            other = self.author if user == self.reviewer else self.reviewer
            self.assertNotContains(response, other.email)
            self.assertNotContains(response, other.first_name)

    def test_dashboard_shows_neutral_labels(self):
        self.client.force_login(self.reviewer)
        response = self.client.get('/courses/privacy-course/reviews')
        self.assertContains(response, 'by Reviewer 1')
        self.assertContains(response, 'Submission 1')
        self.assertNotContains(response, self.author.email)

    def test_review_form_hides_identities(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(
            f'/courses/privacy-course/reviews/{self.author_submission.pk}'
        )
        self.assertNotContains(response, AUTHOR_EMAIL)
        self.assertNotContains(response, AUTHOR_USERNAME)
        self.assertContains(response, 'https://github.com/author/project')

    def test_author_review_form_hides_reviewer_identity(self):
        self.client.force_login(self.author)
        response = self.client.get(
            f'/courses/privacy-course/reviews/{self.reviewer_submission.pk}'
        )
        self.assertContains(response, 'Nice work')
        self.assertNotContains(response, REVIEWER_EMAIL)
        self.assertNotContains(response, REVIEWER_USERNAME)
