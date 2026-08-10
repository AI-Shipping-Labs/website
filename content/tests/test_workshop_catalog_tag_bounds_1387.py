"""Regression coverage for bounded Workshop catalog tags (#1387)."""

from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, TestCase, override_settings

from content.models import Workshop
from content.views.workshops import (
    WORKSHOP_CATALOG_BAD_TAG_RESPONSE,
    InvalidWorkshopCatalogTags,
    _build_catalog_filter_url,
    _normalize_workshop_catalog_tags,
)


class WorkshopCatalogTagNormalizerTest(SimpleTestCase):
    def test_normalizes_blank_whitespace_and_exact_duplicates(self):
        normalized, changed = _normalize_workshop_catalog_tags(
            ['', '  agents  ', 'agents', '\t', 'RAG'],
        )

        self.assertEqual(normalized, ['agents', 'RAG'])
        self.assertTrue(changed)

    def test_preserves_two_unique_tags_and_first_occurrence_order(self):
        normalized, changed = _normalize_workshop_catalog_tags(
            ['rag', 'agents'],
        )

        self.assertEqual(normalized, ['rag', 'agents'])
        self.assertFalse(changed)

    def test_accepts_exactly_100_code_points(self):
        normalized, changed = _normalize_workshop_catalog_tags(['x' * 100])

        self.assertEqual(normalized, ['x' * 100])
        self.assertFalse(changed)

    def test_rejects_third_unique_overlength_and_control_values(self):
        invalid_values = (
            ['one', 'two', 'three'],
            ['x' * 101],
            ['valid\x00invalid'],
            ['valid\x7finvalid'],
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(InvalidWorkshopCatalogTags):
                    _normalize_workshop_catalog_tags(values)

    def test_catalog_url_builder_bounds_and_deduplicates_adversarial_tags(self):
        url = _build_catalog_filter_url(
            access_slug='paid',
            skill_level='intermediate',
            selected_tools=['Python', 'Python', 'Docker'],
            selected_tags=['rag', 'rag', 'agents', 'third'],
        )

        self.assertEqual(
            url,
            '/workshops/catalog?access=paid&skill_level=intermediate&'
            'tool=Python&tool=Docker&tag=rag&tag=agents',
        )


@override_settings(ALLOWED_HOSTS=['testserver', 'attacker.localhost'])
class WorkshopCatalogEarlyBoundResponseTest(TestCase):
    url = '/workshops/catalog'

    def _assert_no_catalog_work(self, query):
        with (
            patch(
                'content.views.workshops._build_workshops_catalog_context',
            ) as build_context,
            patch.object(Workshop.objects, 'filter') as workshop_filter,
        ):
            response = self.client.get(f'{self.url}?{query}')

        build_context.assert_not_called()
        workshop_filter.assert_not_called()
        return response

    def test_duplicate_blank_and_whitespace_tags_redirect_before_catalog_work(self):
        response = self._assert_no_catalog_work(
            'access=paid&skill_level=intermediate&tool=Python&tool=Python&'
            'tag=%20rag%20&tag=&tag=rag&unknown=discarded',
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/workshops/catalog?access=paid&skill_level=intermediate&'
            'tool=Python&tag=rag',
        )
        self.assertTrue(response['Location'].startswith('/'))

    def test_two_hundred_duplicate_tags_redirect_with_constant_bounded_location(self):
        query = '&'.join(['tag=rag'] * 200)
        response = self._assert_no_catalog_work(query)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/workshops/catalog?tag=rag')

    def test_untrusted_host_is_never_reflected_in_redirect(self):
        with (
            patch(
                'content.views.workshops._build_workshops_catalog_context',
            ) as build_context,
            patch.object(Workshop.objects, 'filter') as workshop_filter,
        ):
            response = self.client.get(
                f'{self.url}?tag=%20rag%20',
                HTTP_HOST='attacker.localhost',
            )

        build_context.assert_not_called()
        workshop_filter.assert_not_called()
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/workshops/catalog?tag=rag')

    def test_pathological_tags_return_small_noindex_no_store_400(self):
        cases = (
            'tag=one&tag=two&tag=three',
            f'tag={"x" * 101}',
            'tag=valid%00invalid',
            'tag=valid%7Finvalid',
        )

        for query in cases:
            with self.subTest(query=query):
                response = self._assert_no_catalog_work(query)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Location', response)
                self.assertEqual(response['Cache-Control'], 'no-store')
                self.assertEqual(
                    response['X-Robots-Tag'],
                    'noindex, nofollow',
                )
                self.assertEqual(
                    response.content.decode(),
                    WORKSHOP_CATALOG_BAD_TAG_RESPONSE,
                )

    def test_burst_of_over_limit_requests_stays_cheap_then_valid_request_works(self):
        with patch(
            'content.views.workshops._build_workshops_catalog_context',
        ) as build_context:
            for _ in range(50):
                response = self.client.get(
                    f'{self.url}?tag=one&tag=two&tag=three',
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.content.decode(),
                    WORKSHOP_CATALOG_BAD_TAG_RESPONSE,
                )
            build_context.assert_not_called()

        response = self.client.get(f'{self.url}?tag=unknown')
        self.assertEqual(response.status_code, 200)


class WorkshopCatalogBoundedRenderingTest(TestCase):
    url = '/workshops/catalog'

    @classmethod
    def setUpTestData(cls):
        Workshop.objects.create(
            slug='agents-rag',
            title='Agents and RAG',
            status='published',
            date=date(2026, 8, 10),
            tags=['agents', 'rag', 'python'],
        )
        Workshop.objects.create(
            slug='agents-only',
            title='Agents only',
            status='published',
            date=date(2026, 8, 9),
            tags=['agents'],
        )
        Workshop.objects.create(
            slug='hidden-draft',
            title='Hidden draft',
            status='draft',
            date=date(2026, 8, 8),
            tags=['agents', 'rag'],
        )

    def test_two_tag_request_keeps_and_semantics_and_disables_third_tag_links(self):
        response = self.client.get(
            f'{self.url}?tag=agents&tag=rag',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agents and RAG')
        self.assertNotContains(response, 'Agents only')
        self.assertNotContains(response, 'Hidden draft')
        self.assertContains(response, 'aria-disabled="true"')
        self.assertContains(
            response,
            'Remove a selected tag before adding python',
        )
        self.assertNotContains(response, 'tag=agents&amp;tag=rag&amp;tag=python')

        body = response.content.decode()
        for href in _extract_workshop_catalog_hrefs(body):
            tags = parse_qs(urlsplit(href).query).get('tag', [])
            self.assertLessEqual(len(tags), 2, href)
            self.assertEqual(len(tags), len(set(tags)), href)

    def test_unknown_valid_tag_preserves_200_empty_state(self):
        response = self.client.get(f'{self.url}?tag=unknown')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No workshops found')
        self.assertContains(response, 'View all workshops')


def _extract_workshop_catalog_hrefs(body):
    marker = 'href="/workshops/catalog'
    remainder = body
    while marker in remainder:
        remainder = remainder.split(marker, 1)[1]
        suffix, remainder = remainder.split('"', 1)
        yield f'/workshops/catalog{suffix}'.replace('&amp;', '&')
