"""Relocated trailing-slash indexing-policy owner (#1481)."""

from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from website.search_indexing import NOINDEX_ROBOTS_DIRECTIVE

PRODUCTION_SITE_URL = 'https://aishippinglabs.com'


@override_settings(SITE_BASE_URL=PRODUCTION_SITE_URL)
class TrailingSlashIndexingPolicyTest(TestCase):
    """Owns private-target-only noindex on trailing-slash 301s.

    Relocated from Playwright
    ``test_trailing_slash_redirects_apply_noindex_only_to_private_targets``.
    """

    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_trailing_slash_redirects_apply_noindex_only_to_private_targets(self):
        private_paths = (
            '/notifications/',
            '/request-a-call/',
            '/vote/',
            '/member-api/docs/',
            '/courses/example/submit/',
            '/events/1/example/join/',
            '/sprints/example/board/',
        )
        for path in private_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], path.rstrip('/'))
                self.assertEqual(response['X-Robots-Tag'], NOINDEX_ROBOTS_DIRECTIVE)
                self.assertEqual(response.content, b'')

        public_or_unknown_paths = (
            '/courses/example/',
            '/events/1/example/',
            '/sprints/example/',
            '/notifications/future/',
            '/unknown-indexing-route-1379/',
        )
        for path in public_or_unknown_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], path.rstrip('/'))
                self.assertNotIn('X-Robots-Tag', response)
                self.assertEqual(response.content, b'')
