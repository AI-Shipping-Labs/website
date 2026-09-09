from urllib.parse import parse_qs

from django.test import RequestFactory, SimpleTestCase

from studio.utils import (
    studio_listing_querystring,
    studio_pager_querystring,
)


class StudioListingQuerystringTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_listing_filter_encodes_values_and_drops_page(self):
        request = self.factory.get(
            '/studio/users/',
            {
                'q': 'Ada & Bob +1',
                'tag': 'R&D + alumni',
                'page': '4',
            },
        )

        querystring = studio_listing_querystring(
            request,
            filter='paid',
            tag=None,
        )

        self.assertIn('%26', querystring)
        self.assertIn('%2B1', querystring)
        self.assertEqual(
            parse_qs(querystring.removeprefix('?')),
            {'q': ['Ada & Bob +1'], 'filter': ['paid']},
        )

    def test_pager_encodes_filters_and_replaces_page(self):
        request = self.factory.get(
            '/studio/crm/',
            {
                'q': 'R&D + partners',
                'tag': 'Ada & Bob +1',
                'page': '1',
            },
        )

        querystring = studio_pager_querystring(request, 2)

        self.assertEqual(
            parse_qs(querystring.removeprefix('?')),
            {
                'q': ['R&D + partners'],
                'tag': ['Ada & Bob +1'],
                'page': ['2'],
            },
        )
