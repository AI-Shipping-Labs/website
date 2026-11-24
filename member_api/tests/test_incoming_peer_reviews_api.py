"""Incoming member reviews respect release, ownership, scope and anonymity."""

from django.test import TestCase

from accounts.models import MemberAPIKey
from content.models import Cohort, PeerReview, ProjectSubmission
from content.models.peer_review import ProjectState
from content.tests.incoming_peer_review_fixtures import IncomingReviewFixtures


class MemberIncomingReviewTest(IncomingReviewFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _, cls.key = MemberAPIKey.create_for_user(
            user=cls.owner, name='incoming', scopes=['peer_reviews:read'],
        )
        cls.url = f'/member-api/v1/peer-reviews/submissions/{cls.submission.pk}/incoming'

    def _get(self, key=None):
        if key is None:
            key = self.key
        return self.client.get(self.url, HTTP_AUTHORIZATION=f'Token {key}')

    def _release(self):
        self.project.state = ProjectState.COMPLETED
        self.project.save(update_fields=['state'])

    def test_released_owner_sees_completed_anonymous_incoming_reviews(self):
        self._release()
        response = self._get()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['preliminary_score']['total'], 2.5)
        self.assertEqual(len(data['reviews']), 2)
        self.assertEqual(data['reviews'][0]['reviewer_label'], 'Reviewer 1')
        self.assertEqual(data['reviews'][1]['reviewer_label'], 'Reviewer 2')
        self.assertEqual(data['reviews'][0]['feedback'], 'First useful feedback')
        text = response.content.decode()
        self.assertNotIn('Private draft', text)
        self.assertNotIn('reviewer@example.com', text)
        self.assertNotIn('reviewer_id', text)
        for review in data['reviews']:
            self.assertNotIn('reviewer', review)
            self.assertNotIn('id', review)

    def test_dated_unreleased_owner_gets_403_without_feedback(self):
        response = self._get()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'feedback_not_released')
        self.assertNotIn('reviews', response.json())
        self.assertNotIn('preliminary_score', response.json())

    def test_other_student_gets_403_even_after_release(self):
        self._release()
        _, key = MemberAPIKey.create_for_user(user=self.reviewer, name='other')
        response = self._get(key)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'forbidden')
        self.assertNotIn('reviews', response.json())
        self.assertNotIn('First useful feedback', response.content.decode())

    def test_member_key_requires_peer_reviews_read_scope(self):
        self._release()
        _, key = MemberAPIKey.create_for_user(user=self.owner, name='plans', scopes=['plans:read'])
        self.assertEqual(self._get(key).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_disabled_or_inaccessible_course_does_not_release_feedback(self):
        self._release()
        self.course.peer_review_enabled = False
        self.course.save(update_fields=['peer_review_enabled'])
        self.assertEqual(self._get().status_code, 403)
        self.course.peer_review_enabled = True
        self.course.required_level = 30
        self.course.save(update_fields=['peer_review_enabled', 'required_level'])
        self.assertEqual(self._get().status_code, 403)

    def test_self_paced_release_requires_owner_to_complete_assigned_reviews(self):
        cohort = Cohort.objects.create(course=self.course, name='Self-paced', mode='self_paced')
        self.project.cohort = cohort
        self.project.save(update_fields=['cohort'])
        self.assertEqual(self._get().status_code, 403)
        other_submission = ProjectSubmission.objects.create(
            course=self.course, course_project=self.project, user=self.reviewer,
        )
        outgoing = PeerReview.objects.create(submission=other_submission, reviewer=self.owner)
        self.assertEqual(self._get().status_code, 403)
        outgoing.is_complete = True
        outgoing.criteria_scores = {'quality': 1, 'extras': []}
        outgoing.save(update_fields=['is_complete', 'criteria_scores'])
        self.assertEqual(self._get().status_code, 200)
        outgoing.criteria_scores = {}
        outgoing.save(update_fields=['criteria_scores'])
        self.assertEqual(self._get().status_code, 403)

    def test_legacy_release_follows_submission_completion(self):
        self.submission.course_project = None
        self.submission.save(update_fields=['course_project'])
        self.assertEqual(self._get().status_code, 403)
        self.submission.status = 'review_complete'
        self.submission.save(update_fields=['status'])
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['preliminary_score']['total'])
