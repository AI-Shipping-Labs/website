"""Regression coverage for bounded public tag pages."""

from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from content.models import Article


class TagPageBoundsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Agents article', slug='agents-article',
            date=date(2026, 1, 1), tags=['agents'], published=True,
        )
        Article.objects.create(
            title='Other article', slug='other-article',
            date=date(2026, 1, 2), tags=['other'], published=True,
        )

    def setUp(self):
        cache.clear()

    def test_unknown_tag_404s_before_loading_content(self):
        with (
            patch('content.views.tags.known_public_tags', return_value={'agents'}),
            patch('content.views.tags._tagged_queryset') as tagged_queryset,
        ):
            response = self.client.get('/tags/not-a-real-tag')

        self.assertEqual(response.status_code, 404)
        tagged_queryset.assert_not_called()

    def test_known_tag_returns_only_matching_published_content(self):
        with patch(
            'content.views.tags.known_public_tags', return_value={'agents'},
        ):
            response = self.client.get('/tags/agents')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agents article')
        self.assertNotContains(response, 'Other article')
        self.assertEqual(response.context['result_count'], 1)

    @override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
    def test_anonymous_index_and_detail_responses_are_reused(self):
        cache.clear()
        with patch(
            'content.views.tags.known_public_tags', return_value={'agents'},
        ):
            self.assertEqual(self.client.get('/tags').status_code, 200)
            self.assertEqual(self.client.get('/tags/agents').status_code, 200)

        with (
            patch('content.views.tags._collect_all_tags', side_effect=AssertionError),
            patch('content.views.tags.known_public_tags', side_effect=AssertionError),
            patch('content.views.tags._tagged_queryset', side_effect=AssertionError),
        ):
            self.assertEqual(self.client.get('/tags').status_code, 200)
            self.assertEqual(self.client.get('/tags/agents').status_code, 200)
