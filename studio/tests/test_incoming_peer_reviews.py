"""Studio submission disclosures use the shared incoming score breakdown."""

from django.test import TestCase

from accounts.templatetags.date_formatting import operator_datetime_tz
from content.tests.incoming_peer_review_fixtures import IncomingReviewFixtures


class StudioIncomingReviewTest(IncomingReviewFixtures, TestCase):
    def test_submission_disclosure_has_score_and_review_details(self):
        self.client.force_login(self.staff)
        response = self.client.get(f'/studio/courses/{self.course.pk}/peer-reviews')
        item = response.context['cohort_groups'][0]['items'][0]
        self.assertEqual(item['incoming']['preliminary_score']['total'], 2.5)
        self.assertEqual(item['incoming']['completed_reviews_count'], 2)
        self.assertEqual(len(item['incoming']['reviews']), 3)
        self.assertContains(response, operator_datetime_tz(self.first_review.assigned_at))
        self.assertContains(response, operator_datetime_tz(self.first_review.completed_at))
        self.assertTemplateUsed(response, 'studio/courses/_incoming_peer_reviews.html')
        self.assertContains(response, '<dd class="text-foreground">1.5 / 3</dd>', html=True)
        self.assertContains(response, '<p class="mt-2 text-sm text-foreground whitespace-pre-line" data-testid="review-feedback">First useful feedback</p>', html=True)

    def test_no_reviews_has_explicit_empty_state(self):
        self.submission.reviews.all().delete()
        self.client.force_login(self.staff)
        response = self.client.get(f'/studio/courses/{self.course.pk}/peer-reviews')
        item = response.context['cohort_groups'][0]['items'][0]
        self.assertIsNone(item['incoming']['preliminary_score']['total'])
        self.assertContains(response, 'No preliminary score available yet.')
        self.assertContains(response, 'No reviews yet')
