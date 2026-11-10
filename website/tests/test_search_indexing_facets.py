from django.test import RequestFactory, SimpleTestCase, override_settings

from website.search_indexing import (
    faceted_listing_robots_disallow_patterns,
    request_indexing_disabled,
)


@override_settings(
    SITE_BASE_URL='https://aishippinglabs.com',
    ALLOWED_HOSTS=['aishippinglabs.com'],
)
class CourseFacetIndexingTest(SimpleTestCase):
    def test_tag_and_page_course_listings_are_not_indexable(self):
        factory = RequestFactory()

        for query in ('tag=agents', 'page=2'):
            with self.subTest(query=query):
                request = factory.get(f'/courses?{query}', HTTP_HOST='aishippinglabs.com')

                self.assertTrue(request_indexing_disabled(request))

    def test_course_tag_and_page_are_disallowed_in_robots_patterns(self):
        patterns = set(faceted_listing_robots_disallow_patterns())

        self.assertIn('/courses?*tag=', patterns)
        self.assertIn('/courses?*page=', patterns)
