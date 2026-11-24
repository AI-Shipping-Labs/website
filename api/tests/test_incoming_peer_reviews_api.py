"""Staff incoming breakdowns, scoping, authorization and no side effects."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from accounts.models import Token
from content.models import Course, PeerReview, ProjectSubmission
from content.tests.incoming_peer_review_fixtures import IncomingReviewFixtures


class StaffIncomingReviewTest(IncomingReviewFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.token = Token.objects.create(user=cls.staff, name='incoming staff')
        cls.url = '/api/courses/ai-buildcamp/peer-reviews'

    def _get(self, params):
        return self.client.get(self.url, params, HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_student_name_filter_returns_incoming_breakdown_and_preserves_counts(self):
        before = list(PeerReview.objects.values('pk', 'is_complete', 'criteria_scores', 'feedback'))
        with patch('content.services.peer_review_service.PeerReviewService.check_and_update_submission_status') as mutate:
            response = self._get({'project': 'attempt-1', 'student': 'George Cosmin Taschina'})
        data = response.json()
        self.assertEqual(data['projects'][0]['completed_reviews_count'], 2)
        self.assertEqual(data['pagination']['total'], 1)
        item = data['submissions'][0]
        self.assertEqual(item['student']['email'], 'george@example.com')
        self.assertEqual(item['preliminary_score']['total'], 2.5)
        self.assertEqual(item['reviews'][0]['reviewer']['email'], 'reviewer@example.com')
        self.assertEqual(item['reviews'][0]['feedback'], 'First useful feedback')
        self.assertEqual(item['reviews'][2]['status'], 'pending')
        mutate.assert_not_called()
        self.assertEqual(list(PeerReview.objects.values('pk', 'is_complete', 'criteria_scores', 'feedback')), before)
        self.assertEqual(len(mail.outbox), 0)

    def test_default_response_remains_counts_only(self):
        data = self._get({}).json()
        self.assertNotIn('submissions', data)
        self.assertNotIn('pagination', data)

    def test_submission_cannot_escape_course_or_project_scope(self):
        course = Course.objects.create(slug='other', title='Other')
        other = ProjectSubmission.objects.create(course=course, user=self.owner)
        response = self._get({'submission': other.pk, 'project': 'attempt-1'})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('submissions', response.json())
        self.assertEqual(self._get({'submission': self.submission.pk, 'student': 'nobody'}).status_code, 404)

    def test_invalid_page_or_submission_returns_422(self):
        for params in ({'details': '1', 'page': '-1'}, {'submission': 'bad'}):
            self.assertEqual(self._get(params).status_code, 422)

    def test_staff_token_required_for_details(self):
        self.assertEqual(self.client.get(self.url, {'details': '1'}).status_code, 401)
        self.reviewer.is_staff = True
        self.reviewer.save(update_fields=['is_staff'])
        token = Token.objects.create(user=self.reviewer, name='initially staff')
        self.reviewer.is_staff = False
        self.reviewer.save(update_fields=['is_staff'])
        response = self.client.get(self.url, {'details': '1'}, HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('submissions', response.json())

    def test_details_paginate_submission_collection(self):
        for number in range(20):
            user = get_user_model().objects.create_user(email=f'page{number}@example.com')
            ProjectSubmission.objects.create(course=self.course, user=user)
        data = self._get({'details': '1', 'page': 1}).json()
        self.assertEqual(data['pagination']['page_size'], 20)
        self.assertEqual(data['pagination']['total'], 21)
        self.assertEqual(len(data['submissions']), 20)
        second = self._get({'details': '1', 'page': 2}).json()
        self.assertEqual(len(second['submissions']), 1)
        first_ids = [item['submission_id'] for item in data['submissions']]
        self.assertNotIn(second['submissions'][0]['submission_id'], first_ids)
        self.assertEqual(self._get({'details': '1', 'page': 3}).status_code, 404)
