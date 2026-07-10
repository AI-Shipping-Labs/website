import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from content.models import MarketingPage
from content.models.marketing_page import is_reserved_marketing_page_path


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class MarketingPageModelTest(TestCase):
    def test_content_id_is_generated_and_markdown_is_rendered(self):
        page = MarketingPage.objects.create(
            title='Community Story',
            public_path='/community-story',
            content_markdown='# Community Story\n\nVisit https://example.com',
            status='published',
        )

        self.assertIsInstance(page.content_id, uuid.UUID)
        self.assertIn('<p>Visit <a href="https://example.com"', page.content_html)
        self.assertNotIn('<h1>Community Story</h1>', page.content_html)
        self.assertIsNotNone(page.published_at)

    def test_public_path_validation_rejects_invalid_or_reserved_paths(self):
        invalid_paths = [
            '',
            'community-story',
            '/community-story/',
            '/../secret',
            '/campaign?utm=x',
            '/campaign#section',
            '/events',
            '/api/marketing-pages',
            '/studio/marketing-pages',
            '/about',
            '/register',
            '/learning-path/ai-engineer',
            '/community/slack',
            '/sitemap.xml',
        ]

        for path in invalid_paths:
            with self.subTest(path=path):
                page = MarketingPage(
                    title='Invalid',
                    public_path=path,
                    content_markdown='Body',
                )
                with self.assertRaises(ValidationError):
                    page.save()

    def test_reserved_path_guard_rejects_registered_non_fallback_routes(self):
        reserved_paths = [
            '/about',
            '/register',
            '/learning-path/ai-engineer',
            '/community/slack',
            '/sitemap.xml',
        ]

        for path in reserved_paths:
            with self.subTest(path=path):
                self.assertTrue(is_reserved_marketing_page_path(path))

        self.assertFalse(is_reserved_marketing_page_path('/community-story'))

    def test_duplicate_public_path_is_rejected(self):
        MarketingPage.objects.create(
            title='First',
            public_path='/campaign',
            content_markdown='First',
        )
        with self.assertRaises(ValidationError) as ctx:
            MarketingPage.objects.create(
                title='Second',
                public_path='/campaign',
                content_markdown='Second',
            )

        self.assertIn('already uses this public path', str(ctx.exception))


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class MarketingPagePublicRouteTest(TestCase):
    def test_published_page_renders_through_final_fallback_without_taxonomy_chrome(self):
        MarketingPage.objects.create(
            title='AI Shipping Labs Community Story',
            public_path='/community-story',
            description='Standalone community orientation.',
            meta_description='Search description for the community story.',
            content_markdown='## Build with others\n\nStandalone body.',
            status='published',
            nav_section='community',
            nav_label='Community Story',
        )

        response = self.client.get('/community-story')
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI Shipping Labs Community Story')
        self.assertContains(response, 'Standalone body.')
        self.assertContains(response, 'data-testid="nav-community-link-marketing-1"')
        self.assertContains(response, 'Community Story')
        self.assertContains(
            response,
            '<link rel="canonical" href="https://aishippinglabs.com/community-story">',
        )
        self.assertContains(
            response,
            '<meta name="description" content="Search description for the community story.">',
        )
        self.assertNotIn('Back to Blog', body)
        self.assertNotIn('Back to Events', body)
        self.assertNotIn('Register for this event', body)
        self.assertNotIn('Upgrade to', body)

    def test_unknown_draft_and_first_class_routes_do_not_use_fallback(self):
        MarketingPage.objects.create(
            title='Draft Campaign',
            public_path='/draft-campaign',
            content_markdown='Draft body.',
            status='draft',
        )
        MarketingPage.objects.create(
            title='Valid Custom Page',
            public_path='/valid-custom-page',
            content_markdown='Custom fallback body.',
            status='published',
        )

        self.assertEqual(self.client.get('/missing-campaign').status_code, 404)
        self.assertEqual(self.client.get('/draft-campaign').status_code, 404)

        events_response = self.client.get('/events')
        self.assertEqual(events_response.status_code, 200)
        self.assertNotContains(events_response, 'Custom fallback body.')

        api_response = self.client.get('/api/marketing-pages')
        self.assertEqual(api_response.status_code, 401)

    def test_draft_preview_is_private_noindex_and_excluded_from_nav_and_sitemap(self):
        draft = MarketingPage.objects.create(
            title='Draft Campaign',
            public_path='/draft-campaign',
            content_markdown='Draft preview body.',
            status='draft',
            nav_section='resources',
            nav_label='Draft Campaign',
            show_in_sitemap=True,
        )

        preview = self.client.get(draft.get_preview_url())
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        self.assertContains(preview, 'Draft preview')
        self.assertContains(preview, 'name="robots" content="noindex,nofollow,noarchive"')
        self.assertNotContains(preview, '<link rel="canonical"')
        self.assertEqual(self.client.get('/draft-campaign').status_code, 404)

        sitemap = self.client.get('/sitemap.xml')
        self.assertNotContains(sitemap, '/draft-campaign')

        visible = MarketingPage.objects.create(
            title='Visible Resources',
            public_path='/visible-resources',
            content_markdown='Visible body.',
            status='published',
            nav_section='resources',
            nav_label='Visible Resources',
        )
        response = self.client.get(visible.get_absolute_url())
        self.assertContains(response, 'Visible Resources')
        self.assertNotContains(response, 'Draft Campaign')

    def test_sitemap_includes_only_published_opted_in_pages(self):
        MarketingPage.objects.create(
            title='Indexed',
            public_path='/indexed-page',
            content_markdown='Indexed body.',
            status='published',
            show_in_sitemap=True,
        )
        MarketingPage.objects.create(
            title='Hidden',
            public_path='/hidden-page',
            content_markdown='Hidden body.',
            status='published',
            show_in_sitemap=False,
        )

        response = self.client.get('/sitemap.xml')
        self.assertContains(response, '/indexed-page')
        self.assertNotContains(response, '/hidden-page')
