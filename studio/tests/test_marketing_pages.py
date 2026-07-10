from django.test import TestCase

from content.models import MarketingPage
from tests.fixtures import StaffUserMixin


class StudioMarketingPageTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_staff_can_create_publish_and_view_manual_marketing_page(self):
        response = self.client.post('/studio/marketing-pages/new', {
            'title': 'AI Shipping Labs Community Story',
            'public_path': '/community-story',
            'description': 'Standalone page.',
            'meta_description': 'Standalone search text.',
            'content_markdown': '## Hello\n\nCommunity body.',
            'status': 'published',
            'show_in_sitemap': 'on',
            'nav_section': 'community',
            'nav_label': 'Community Story',
            'nav_order': '10',
            'cover_image_url': '',
            'tags': 'Community, Launch',
        })

        page = MarketingPage.objects.get(public_path='/community-story')
        self.assertRedirects(
            response,
            f'/studio/marketing-pages/{page.pk}/edit',
            fetch_redirect_response=False,
        )
        self.assertEqual(page.status, 'published')
        self.assertEqual(page.nav_section, 'community')
        self.assertEqual(page.tags, ['community', 'launch'])

        list_response = self.client.get('/studio/marketing-pages/')
        self.assertContains(list_response, 'AI Shipping Labs Community Story')
        self.assertContains(list_response, 'View on site')

    def test_route_collision_is_rejected_before_save(self):
        response = self.client.post('/studio/marketing-pages/new', {
            'title': 'Events Collision',
            'public_path': '/events',
            'content_markdown': 'Collision',
            'status': 'published',
            'nav_section': 'none',
            'nav_order': '0',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'conflicts with an existing route or reserved prefix',
        )
        self.assertFalse(MarketingPage.objects.filter(title='Events Collision').exists())

    def test_synced_page_is_visible_with_readonly_source_affordances(self):
        page = MarketingPage.objects.create(
            title='Synced Launch Recap',
            public_path='/launch-recap',
            content_markdown='Synced body.',
            status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='pages/launch-recap.md',
            source_commit='abc1234',
        )

        response = self.client.get(f'/studio/marketing-pages/{page.pk}/edit')
        self.assertContains(response, 'Source-managed marketing page')
        self.assertContains(response, 'Edit on GitHub')
        self.assertContains(response, 'Re-sync source')
        self.assertContains(response, 'disabled')

        post_response = self.client.post(f'/studio/marketing-pages/{page.pk}/edit', {
            'title': 'Changed',
            'public_path': '/changed',
        })
        self.assertEqual(post_response.status_code, 403)
        page.refresh_from_db()
        self.assertEqual(page.title, 'Synced Launch Recap')
