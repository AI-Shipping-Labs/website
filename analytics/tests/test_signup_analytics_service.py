"""Focused contracts for signup analytics reporting helpers."""

from django.test import SimpleTestCase

from analytics.services.signup_analytics import content_category_for_path


class ContentCategoryForPathTest(SimpleTestCase):

    def test_membership_is_the_canonical_pricing_category_route(self):
        self.assertEqual(content_category_for_path('/membership'), 'Pricing')
        self.assertEqual(
            content_category_for_path('/membership?utm_source=newsletter'),
            'Pricing',
        )
