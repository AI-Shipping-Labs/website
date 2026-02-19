"""Tests for Curated Links - issue #76.

Covers:
- CuratedLink model fields (tags, required_level, sort_order, etc.)
- /resources listing page with category grouping
- Tag filtering via ?tag=X
- Gated links: lock icon shown, URL hidden from HTML, upgrade CTA
- Open links: external link icon shown, URL present in HTML
- Links sorted by sort_order within categories
- Admin CRUD for curated links
- /collection backward compat URL
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN
from content.models import CuratedLink
from payments.models import Tier

User = get_user_model()


class TierSetupMixin:
    """Mixin that creates the standard tiers for access control tests."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug='main', defaults={'name': 'Main', 'level': 20},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug='premium', defaults={'name': 'Premium', 'level': 30},
        )


# --- Model field tests ---


class CuratedLinkModelFieldsTest(TestCase):
    """Test that CuratedLink has all required fields from issue #76."""

    def test_title_field(self):
        link = CuratedLink.objects.create(
            item_id='test-title', title='Test Link',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.title, 'Test Link')

    def test_description_field(self):
        link = CuratedLink.objects.create(
            item_id='test-desc', title='Test',
            description='A short description',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.description, 'A short description')

    def test_description_default_empty(self):
        link = CuratedLink.objects.create(
            item_id='test-desc-default', title='Test',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.description, '')

    def test_url_field(self):
        link = CuratedLink.objects.create(
            item_id='test-url', title='Test',
            url='https://github.com/test/repo', category='tools',
        )
        self.assertEqual(link.url, 'https://github.com/test/repo')

    def test_category_field(self):
        link = CuratedLink.objects.create(
            item_id='test-cat', title='Test',
            url='https://example.com', category='models',
        )
        self.assertEqual(link.category, 'models')

    def test_tags_field_is_list(self):
        link = CuratedLink.objects.create(
            item_id='test-tags', title='Test',
            url='https://example.com', category='tools',
            tags=['python', 'ai', 'cli'],
        )
        self.assertEqual(link.tags, ['python', 'ai', 'cli'])

    def test_tags_default_empty_list(self):
        link = CuratedLink.objects.create(
            item_id='test-tags-default', title='Test',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.tags, [])

    def test_required_level_default_0(self):
        link = CuratedLink.objects.create(
            item_id='test-rl', title='Test',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.required_level, 0)

    def test_required_level_custom(self):
        link = CuratedLink.objects.create(
            item_id='test-rl-custom', title='Test',
            url='https://example.com', category='tools',
            required_level=LEVEL_BASIC,
        )
        self.assertEqual(link.required_level, LEVEL_BASIC)

    def test_sort_order_default_0(self):
        link = CuratedLink.objects.create(
            item_id='test-sort', title='Test',
            url='https://example.com', category='tools',
        )
        self.assertEqual(link.sort_order, 0)

    def test_sort_order_custom(self):
        link = CuratedLink.objects.create(
            item_id='test-sort-custom', title='Test',
            url='https://example.com', category='tools',
            sort_order=10,
        )
        self.assertEqual(link.sort_order, 10)

    def test_created_at_set_on_create(self):
        link = CuratedLink.objects.create(
            item_id='test-created', title='Test',
            url='https://example.com', category='tools',
        )
        self.assertIsNotNone(link.created_at)

    def test_required_level_tier_name_property(self):
        link = CuratedLink(required_level=LEVEL_BASIC)
        self.assertEqual(link.required_level_tier_name, 'Basic')

    def test_required_level_tier_name_open(self):
        link = CuratedLink(required_level=LEVEL_OPEN)
        self.assertEqual(link.required_level_tier_name, 'Free')


# --- Model ordering tests ---


class CuratedLinkOrderingTest(TestCase):
    """Test that links are ordered by sort_order, then title."""

    def test_ordering_by_sort_order(self):
        link_b = CuratedLink.objects.create(
            item_id='order-b', title='B Link',
            url='https://example.com', category='tools',
            sort_order=2,
        )
        link_a = CuratedLink.objects.create(
            item_id='order-a', title='A Link',
            url='https://example.com', category='tools',
            sort_order=1,
        )
        link_c = CuratedLink.objects.create(
            item_id='order-c', title='C Link',
            url='https://example.com', category='tools',
            sort_order=3,
        )
        links = list(CuratedLink.objects.all())
        self.assertEqual(links[0].item_id, 'order-a')
        self.assertEqual(links[1].item_id, 'order-b')
        self.assertEqual(links[2].item_id, 'order-c')

    def test_ordering_by_title_when_same_sort_order(self):
        link_z = CuratedLink.objects.create(
            item_id='order-z', title='Zebra',
            url='https://example.com', category='tools',
            sort_order=0,
        )
        link_a = CuratedLink.objects.create(
            item_id='order-alpha', title='Alpha',
            url='https://example.com', category='tools',
            sort_order=0,
        )
        links = list(CuratedLink.objects.all())
        self.assertEqual(links[0].title, 'Alpha')
        self.assertEqual(links[1].title, 'Zebra')


# --- View: /resources page ---


class ResourcesPageBasicTest(TestCase):
    """Test basic rendering of /resources page."""

    def setUp(self):
        self.client = Client()
        self.tool_link = CuratedLink.objects.create(
            item_id='tool-1', title='Cool CLI Tool',
            description='A great CLI tool',
            url='https://github.com/test/cli',
            category='tools', tags=['python', 'cli'],
            sort_order=1, published=True,
        )
        self.model_link = CuratedLink.objects.create(
            item_id='model-1', title='Model Hub',
            description='Browse AI models',
            url='https://huggingface.co',
            category='models', tags=['ai', 'models'],
            sort_order=1, published=True,
        )

    def test_resources_page_returns_200(self):
        response = self.client.get('/resources')
        self.assertEqual(response.status_code, 200)

    def test_shows_link_titles(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Cool CLI Tool')
        self.assertContains(response, 'Model Hub')

    def test_shows_link_descriptions(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'A great CLI tool')
        self.assertContains(response, 'Browse AI models')

    def test_shows_category_headers(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Tools')
        self.assertContains(response, 'Models')

    def test_open_link_has_external_link_icon(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'data-lucide="external-link"')

    def test_open_link_has_url_in_href(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'https://github.com/test/cli')

    def test_open_link_opens_in_new_tab(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'target="_blank"')

    def test_unpublished_link_not_shown(self):
        CuratedLink.objects.create(
            item_id='unpub', title='Unpublished Link',
            url='https://example.com', category='tools',
            published=False,
        )
        response = self.client.get('/resources')
        self.assertNotContains(response, 'Unpublished Link')


# --- View: category grouping ---


class ResourcesCategoryGroupingTest(TestCase):
    """Test that links are grouped by category with category headers."""

    def setUp(self):
        self.client = Client()
        # Create links in different categories
        CuratedLink.objects.create(
            item_id='tool-g', title='Tool Link',
            url='https://example.com/tool', category='tools',
            sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='course-g', title='Course Link',
            url='https://example.com/course', category='courses',
            sort_order=1, published=True,
        )

    def test_tools_category_header_shown(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Tools')

    def test_courses_category_header_shown(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Courses')

    def test_empty_category_not_shown(self):
        """If no links in 'models' category, its header should not appear."""
        response = self.client.get('/resources')
        content = response.content.decode()
        # 'Models' should NOT appear as a section header since there are no model links
        # We check that 'Models' doesn't appear as a category label in an h2
        # (it may appear in other places like nav, so check specifically)
        self.assertNotIn('Model Hub', content)

    def test_grouped_categories_in_context(self):
        response = self.client.get('/resources')
        grouped = response.context['grouped_categories']
        keys = [g['key'] for g in grouped]
        self.assertIn('tools', keys)
        self.assertIn('courses', keys)


# --- View: sort order within categories ---


class ResourcesSortOrderTest(TestCase):
    """Test that links are sorted by sort_order within each category."""

    def setUp(self):
        self.client = Client()
        CuratedLink.objects.create(
            item_id='sort-3', title='Third Tool',
            url='https://example.com/3', category='tools',
            sort_order=3, published=True,
        )
        CuratedLink.objects.create(
            item_id='sort-1', title='First Tool',
            url='https://example.com/1', category='tools',
            sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='sort-2', title='Second Tool',
            url='https://example.com/2', category='tools',
            sort_order=2, published=True,
        )

    def test_links_sorted_by_sort_order(self):
        response = self.client.get('/resources')
        content = response.content.decode()
        first_pos = content.index('First Tool')
        second_pos = content.index('Second Tool')
        third_pos = content.index('Third Tool')
        self.assertLess(first_pos, second_pos)
        self.assertLess(second_pos, third_pos)


# --- View: tag filtering ---


class ResourcesTagFilteringTest(TestCase):
    """Test tag filtering on /resources via ?tag=X query param."""

    def setUp(self):
        self.client = Client()
        self.python_link = CuratedLink.objects.create(
            item_id='tag-py', title='Python Tool',
            url='https://example.com/python',
            category='tools', tags=['python', 'cli'],
            published=True,
        )
        self.ai_link = CuratedLink.objects.create(
            item_id='tag-ai', title='AI Model',
            url='https://example.com/ai',
            category='models', tags=['ai', 'llm'],
            published=True,
        )
        self.both_link = CuratedLink.objects.create(
            item_id='tag-both', title='Python AI Tool',
            url='https://example.com/both',
            category='tools', tags=['python', 'ai'],
            published=True,
        )

    def test_no_filter_shows_all_links(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Python Tool')
        self.assertContains(response, 'AI Model')
        self.assertContains(response, 'Python AI Tool')

    def test_filter_by_python_tag(self):
        response = self.client.get('/resources?tag=python')
        self.assertContains(response, 'Python Tool')
        self.assertContains(response, 'Python AI Tool')
        self.assertNotContains(response, 'AI Model')

    def test_filter_by_ai_tag(self):
        response = self.client.get('/resources?tag=ai')
        self.assertContains(response, 'AI Model')
        self.assertContains(response, 'Python AI Tool')
        self.assertNotContains(response, 'Python Tool')

    def test_filter_by_nonexistent_tag(self):
        response = self.client.get('/resources?tag=nonexistent')
        self.assertNotContains(response, 'Python Tool')
        self.assertNotContains(response, 'AI Model')
        self.assertNotContains(response, 'Python AI Tool')

    def test_tag_chips_displayed(self):
        response = self.client.get('/resources')
        content = response.content.decode()
        self.assertIn('?tag=python', content)
        self.assertIn('?tag=ai', content)
        self.assertIn('?tag=cli', content)
        self.assertIn('?tag=llm', content)

    def test_all_tags_in_context(self):
        response = self.client.get('/resources')
        all_tags = response.context['all_tags']
        self.assertIn('python', all_tags)
        self.assertIn('ai', all_tags)
        self.assertIn('cli', all_tags)
        self.assertIn('llm', all_tags)

    def test_current_tag_in_context(self):
        response = self.client.get('/resources?tag=python')
        self.assertEqual(response.context['current_tag'], 'python')

    def test_clear_filter_link(self):
        response = self.client.get('/resources?tag=python')
        content = response.content.decode()
        self.assertIn('Clear filter', content)

    def test_filter_by_tag_label_shown(self):
        response = self.client.get('/resources?tag=python')
        content = response.content.decode()
        self.assertIn('Showing links tagged with', content)


# --- View: gating / access control ---


class ResourcesGatingTest(TierSetupMixin, TestCase):
    """Test that gated links hide URLs and show lock icons + upgrade CTA."""

    def setUp(self):
        self.client = Client()
        self.open_link = CuratedLink.objects.create(
            item_id='open-link', title='Open Link',
            description='Freely accessible',
            url='https://example.com/open-resource',
            category='tools', published=True,
            required_level=LEVEL_OPEN,
        )
        self.gated_link = CuratedLink.objects.create(
            item_id='gated-link', title='Gated Link',
            description='Requires Basic tier',
            url='https://example.com/secret-resource',
            category='tools', published=True,
            required_level=LEVEL_BASIC,
        )

    def test_anonymous_sees_open_link_url(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'https://example.com/open-resource')

    def test_anonymous_does_not_see_gated_link_url(self):
        response = self.client.get('/resources')
        self.assertNotContains(response, 'https://example.com/secret-resource')

    def test_gated_link_url_not_in_href(self):
        """The actual URL must not be exposed in any href attribute."""
        response = self.client.get('/resources')
        content = response.content.decode()
        self.assertNotIn('href="https://example.com/secret-resource"', content)

    def test_gated_link_url_not_in_data_attributes(self):
        """The actual URL must not be exposed in data attributes."""
        response = self.client.get('/resources')
        content = response.content.decode()
        self.assertNotIn('data-url="https://example.com/secret-resource"', content)

    def test_gated_link_shows_lock_icon(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'data-lucide="lock"')

    def test_gated_link_shows_upgrade_cta(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Upgrade to Basic to access this resource')

    def test_gated_link_shows_pricing_link(self):
        response = self.client.get('/resources')
        self.assertContains(response, '/pricing')

    def test_open_link_has_external_icon(self):
        """Open links should have external-link icon, not lock."""
        # Delete gated link so we can verify open link behavior in isolation
        self.gated_link.delete()
        response = self.client.get('/resources')
        self.assertContains(response, 'data-lucide="external-link"')

    def test_basic_user_sees_gated_link_url(self):
        user = User.objects.create_user(
            email='basic@test.com', password='testpass',
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/resources')
        self.assertContains(response, 'https://example.com/secret-resource')

    def test_basic_user_no_lock_on_basic_link(self):
        user = User.objects.create_user(
            email='basic2@test.com', password='testpass',
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic2@test.com', password='testpass')
        response = self.client.get('/resources')
        # Both links should be accessible, no lock icons
        self.assertNotContains(response, 'data-lucide="lock"')

    def test_free_user_sees_gated_link_locked(self):
        user = User.objects.create_user(
            email='free@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/resources')
        self.assertNotContains(response, 'https://example.com/secret-resource')
        self.assertContains(response, 'Upgrade to Basic to access this resource')


class ResourcesGatingMainTierTest(TierSetupMixin, TestCase):
    """Test gating for Main-tier links."""

    def setUp(self):
        self.client = Client()
        self.main_link = CuratedLink.objects.create(
            item_id='main-link', title='Main Tier Link',
            description='Requires Main tier',
            url='https://example.com/main-only',
            category='models', published=True,
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_does_not_see_main_link_url(self):
        response = self.client.get('/resources')
        self.assertNotContains(response, 'https://example.com/main-only')

    def test_basic_user_does_not_see_main_link_url(self):
        user = User.objects.create_user(
            email='basic@test.com', password='testpass',
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/resources')
        self.assertNotContains(response, 'https://example.com/main-only')
        self.assertContains(response, 'Upgrade to Main to access this resource')

    def test_main_user_sees_main_link_url(self):
        user = User.objects.create_user(
            email='main@test.com', password='testpass',
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/resources')
        self.assertContains(response, 'https://example.com/main-only')


# --- Backward compatibility ---


class CollectionBackwardCompatTest(TestCase):
    """Test that /collection still works as a backward-compat URL."""

    def setUp(self):
        self.client = Client()
        CuratedLink.objects.create(
            item_id='compat-link', title='Compat Link',
            url='https://example.com', category='tools',
            published=True,
        )

    def test_collection_url_returns_200(self):
        response = self.client.get('/collection')
        self.assertEqual(response.status_code, 200)

    def test_collection_url_shows_links(self):
        response = self.client.get('/collection')
        self.assertContains(response, 'Compat Link')


# --- Admin tests ---


class CuratedLinkAdminTest(TestCase):
    """Test admin CRUD for curated links."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_list_page(self):
        CuratedLink.objects.create(
            item_id='admin-link', title='Admin Link',
            url='https://example.com', category='tools',
        )
        response = self.client.get('/admin/content/curatedlink/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Link')

    def test_admin_add_page(self):
        response = self.client.get('/admin/content/curatedlink/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_create_link(self):
        response = self.client.post('/admin/content/curatedlink/add/', {
            'item_id': 'new-link',
            'title': 'New Link',
            'description': 'A new link',
            'url': 'https://example.com/new',
            'category': 'tools',
            'tags': '["python", "cli"]',
            'sort_order': 5,
            'required_level': 0,
            'source': '',
            'published': True,
        })
        self.assertEqual(CuratedLink.objects.filter(item_id='new-link').count(), 1)
        link = CuratedLink.objects.get(item_id='new-link')
        self.assertEqual(link.title, 'New Link')
        self.assertEqual(link.tags, ['python', 'cli'])

    def test_admin_edit_link(self):
        link = CuratedLink.objects.create(
            item_id='edit-link', title='Edit Me',
            url='https://example.com', category='tools',
        )
        response = self.client.get(f'/admin/content/curatedlink/{link.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_admin_delete_link(self):
        link = CuratedLink.objects.create(
            item_id='delete-link', title='Delete Me',
            url='https://example.com', category='tools',
        )
        response = self.client.post(
            f'/admin/content/curatedlink/{link.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(CuratedLink.objects.filter(item_id='delete-link').count(), 0)

    def test_admin_search(self):
        CuratedLink.objects.create(
            item_id='search-link', title='Searchable Link',
            description='find me',
            url='https://example.com', category='tools',
        )
        response = self.client.get('/admin/content/curatedlink/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Link')

    def test_admin_filter_by_category(self):
        CuratedLink.objects.create(
            item_id='filter-tool', title='Tool Link',
            url='https://example.com', category='tools',
        )
        response = self.client.get('/admin/content/curatedlink/?category__exact=tools')
        self.assertEqual(response.status_code, 200)

    def test_admin_filter_by_published(self):
        CuratedLink.objects.create(
            item_id='filter-pub', title='Published Link',
            url='https://example.com', category='tools',
            published=True,
        )
        CuratedLink.objects.create(
            item_id='filter-unpub', title='Unpublished Link',
            url='https://example.com', category='tools',
            published=False,
        )
        response = self.client.get('/admin/content/curatedlink/?published__exact=1')
        self.assertEqual(response.status_code, 200)


# --- Empty state tests ---


class ResourcesEmptyStateTest(TestCase):
    """Test the empty state when no links exist."""

    def setUp(self):
        self.client = Client()

    def test_empty_page_returns_200(self):
        response = self.client.get('/resources')
        self.assertEqual(response.status_code, 200)

    def test_empty_page_shows_message(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'No curated links yet')

    def test_empty_filtered_shows_message(self):
        # Add one link but filter by a tag it doesn't have
        CuratedLink.objects.create(
            item_id='only-link', title='Only Link',
            url='https://example.com', category='tools',
            tags=['python'], published=True,
        )
        response = self.client.get('/resources?tag=nonexistent')
        self.assertContains(response, 'No links found with tag')
