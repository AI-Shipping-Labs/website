"""Exact preliminary scoring and read-only review normalization."""

from django.test import TestCase

from content.models import PeerReview
from content.services.incoming_peer_reviews import incoming_breakdown
from content.tests.incoming_peer_review_fixtures import IncomingReviewFixtures


class IncomingScoreTest(IncomingReviewFixtures, TestCase):
    def test_exact_medians_then_sum_exclude_drafts(self):
        data = incoming_breakdown(self.submission, staff=True)
        score = data['preliminary_score']
        self.assertEqual([c['points'] for c in score['criteria']], [1.5, 1.0])
        self.assertEqual(score['total'], 2.5)
        self.assertEqual(score['passing_score'], 3)
        self.assertIs(score['meets_pass_threshold'], False)
        self.assertEqual(score['review_count'], 2)
        self.assertEqual(data['assigned_reviews_count'], 3)
        criterion = data['reviews'][0]['criteria'][0]
        self.assertEqual(criterion['points'], 0)
        self.assertEqual(criterion['max_points'], 3)
        self.assertEqual(criterion['selected_text'], ['Missing'])
        self.assertIsNone(criterion['feedback'])

    def test_invalid_complete_review_is_excluded_without_database_write(self):
        PeerReview.objects.filter(pk=self.first_review.pk).update(criteria_scores={})
        data = incoming_breakdown(self.submission, staff=True)
        self.assertEqual(data['completed_reviews_count'], 1)
        self.assertEqual(data['reviews'][0]['status'], 'pending')
        self.assertEqual(data['preliminary_score']['total'], 3)
        self.first_review.refresh_from_db()
        self.assertTrue(self.first_review.is_complete)

    def test_no_completed_reviews_has_no_invented_total(self):
        self.submission.reviews.update(is_complete=False)
        score = incoming_breakdown(self.submission)['preliminary_score']
        self.assertIsNone(score['total'])
        self.assertIsNone(score['meets_pass_threshold'])
        self.assertEqual(score['review_count'], 0)
        self.assertEqual([c['points'] for c in score['criteria']], [None, None])

    def test_legacy_without_criteria_has_no_structured_total(self):
        self.submission.course_project = None
        data = incoming_breakdown(self.submission, staff=True)
        self.assertIsNone(data['preliminary_score']['total'])
        self.assertEqual(data['reviews'][0]['criteria'], [])
