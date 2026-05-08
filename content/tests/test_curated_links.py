"""Tests for Curated Links - issue #76.

Covers:
- CuratedLink model fields (tags, required_level, sort_order, etc.)
- /resources listing page with category grouping
- Tag filtering via ?tag=X
- Gated links: lock icon shown, URL hidden from HTML, upgrade CTA
- Open links: external link icon shown, URL present in HTML
- Links sorted by sort_order within categories
- Admin CRUD for curated links
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN
from content.models import CuratedLink
from tests.fixtures import TierSetupMixin

User = get_user_model()


# --- Model behavior tests ---


class CuratedLinkModelFieldsTest(TestCase):
    """Test CuratedLink custom behavior."""

    def test_tags_normalized_on_save(self):
        link = CuratedLink.objects.create(
            item_id='test-tags', title='Test',
            url='https://example.com', category='tools',
            tags=[' Python ', 'AI', 'python'],
        )
        self.assertEqual(link.tags, ['python', 'ai'])

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
        CuratedLink.objects.create(
            item_id='order-b', title='B Link',
            url='https://example.com', category='tools',
            sort_order=2,
        )
        CuratedLink.objects.create(
            item_id='order-a', title='A Link',
            url='https://example.com', category='tools',
            sort_order=1,
        )
        CuratedLink.objects.create(
            item_id='order-c', title='C Link',
            url='https://example.com', category='tools',
            sort_order=3,
        )
        links = list(CuratedLink.objects.all())
        self.assertEqual(links[0].item_id, 'order-a')
        self.assertEqual(links[1].item_id, 'order-b')
        self.assertEqual(links[2].item_id, 'order-c')

    def test_ordering_by_title_when_same_sort_order(self):
        CuratedLink.objects.create(
            item_id='order-z', title='Zebra',
            url='https://example.com', category='tools',
            sort_order=0,
        )
        CuratedLink.objects.create(
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

    def test_legacy_tools_and_models_fold_into_other(self):
        """Issue #524: legacy tools/models rows render under the Other
        section without a Tools or Models heading appearing on the page.
        """
        response = self.client.get('/resources')
        content = response.content.decode()
        # Both legacy links visible on the page
        self.assertContains(response, 'Cool CLI Tool')
        self.assertContains(response, 'Model Hub')
        # Other section heading is rendered (match exact <h2>)
        h2_class = 'class="text-xl font-semibold text-foreground"'
        self.assertIn(f'<h2 {h2_class}>Other</h2>', content)
        # No Tools or Models <h2> section heading is rendered
        self.assertNotIn(f'<h2 {h2_class}>Tools</h2>', content)
        self.assertNotIn(f'<h2 {h2_class}>Models</h2>', content)

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

    def test_legacy_tools_link_renders_under_other(self):
        """Issue #524: legacy `tools` rows show up in the Other section,
        not under a Tools heading."""
        response = self.client.get('/resources')
        content = response.content.decode()
        h2_class = 'class="text-xl font-semibold text-foreground"'
        self.assertIn(f'<h2 {h2_class}>Other</h2>', content)
        self.assertNotIn(f'<h2 {h2_class}>Tools</h2>', content)
        # The tool link is still visible to the user
        self.assertContains(response, 'Tool Link')

    def test_courses_category_header_shown(self):
        response = self.client.get('/resources')
        content = response.content.decode()
        h2_class = 'class="text-xl font-semibold text-foreground"'
        self.assertIn(f'<h2 {h2_class}>Courses</h2>', content)

    def test_empty_category_not_shown(self):
        """If no links in 'models' category, its header should not appear."""
        response = self.client.get('/resources')
        content = response.content.decode()
        # 'Models' should NOT appear as a section header since there are no model links
        # We check that 'Models' doesn't appear as a category label in an h2
        # (it may appear in other places like nav, so check specifically)
        self.assertNotIn('Model Hub', content)

    def test_grouped_categories_in_context(self):
        """Issue #524: legacy `tools` rows are grouped under the `other`
        section key. `courses` keeps its own section."""
        response = self.client.get('/resources')
        grouped = response.context['grouped_categories']
        keys = [g['key'] for g in grouped]
        self.assertIn('other', keys)
        self.assertIn('courses', keys)
        self.assertNotIn('tools', keys)
        self.assertNotIn('models', keys)


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

    def test_gated_link_shows_upgrade_cta(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Upgrade to Basic to access this resource')
        self.assertContains(response, '/pricing')

    # Lock-icon and external-link icon string-match tests removed in
    # #261: covered end-to-end by
    # `playwright_tests/test_curated_links.py` and Rule 4 (do not test
    # JS/CSS class strings in templates).

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


# --- Admin tests ---


class CuratedLinkAdminTest(TestCase):
    """Test admin CRUD for curated links."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_create_link(self):
        self.client.post('/admin/content/curatedlink/add/', {
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

    def test_admin_search(self):
        CuratedLink.objects.create(
            item_id='search-link', title='Searchable Link',
            description='find me',
            url='https://example.com', category='tools',
        )
        CuratedLink.objects.create(
            item_id='hidden-link', title='Hidden Link',
            description='different',
            url='https://example.com', category='tools',
        )
        response = self.client.get('/admin/content/curatedlink/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Link')
        self.assertNotContains(response, 'Hidden Link')

    def test_admin_filter_by_category(self):
        CuratedLink.objects.create(
            item_id='filter-tool', title='Tool Link',
            url='https://example.com', category='tools',
        )
        CuratedLink.objects.create(
            item_id='filter-model', title='Model Link',
            url='https://example.com', category='models',
        )
        response = self.client.get('/admin/content/curatedlink/?category__exact=tools')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tool Link')
        self.assertNotContains(response, 'Model Link')

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
        self.assertContains(response, 'Published Link')
        self.assertNotContains(response, 'Unpublished Link')


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
        self.assertContains(response, 'No links found with the selected tags')


# --- Conversions from playwright_tests/test_seo_tags.py (issue #256) ---


class CuratedLinksTagFilterTest(TestCase):
    """Behaviour previously covered by Playwright Scenario 6 on
    /resources. Filtering happens via ?tag= and resolves server-side.
    """

    def test_tag_filter_on_resources(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario6TagFiltersAcrossPages::test_tag_filter_on_resources
        CuratedLink.objects.create(
            item_id='python-cli', title='Python CLI',
            url='https://example.com/python', category='tools',
            tags=['python'], sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='go-toolkit', title='Go Toolkit',
            url='https://example.com/go', category='tools',
            tags=['go'], sort_order=2, published=True,
        )

        response = self.client.get('/resources?tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Python CLI')
        self.assertNotContains(response, 'Go Toolkit')


# --- Issue #524: reorder + workshops/articles + tools/models -> other ---


class CuratedLinkCategoryChoicesIssue524Test(TestCase):
    """Model exposes `workshops` and `articles` choices with their
    labels, descriptions, and icons."""

    def test_workshops_choice_present(self):
        keys = [c[0] for c in CuratedLink.CATEGORY_CHOICES]
        self.assertIn('workshops', keys)

    def test_articles_choice_present(self):
        keys = [c[0] for c in CuratedLink.CATEGORY_CHOICES]
        self.assertIn('articles', keys)

    def test_legacy_tools_and_models_choices_still_valid(self):
        keys = [c[0] for c in CuratedLink.CATEGORY_CHOICES]
        self.assertIn('tools', keys)
        self.assertIn('models', keys)

    def test_workshops_label_and_description(self):
        self.assertEqual(
            CuratedLink.CATEGORY_LABELS['workshops'], 'Workshops'
        )
        self.assertEqual(
            CuratedLink.CATEGORY_DESCRIPTIONS['workshops'],
            'Hands-on workshop materials and tutorials',
        )

    def test_articles_label_and_description(self):
        self.assertEqual(
            CuratedLink.CATEGORY_LABELS['articles'], 'Articles'
        )
        self.assertEqual(
            CuratedLink.CATEGORY_DESCRIPTIONS['articles'],
            'Long-form posts and writeups',
        )

    def test_workshops_icon_is_graduation_cap(self):
        link = CuratedLink(category='workshops')
        self.assertEqual(link.category_icon_name, 'graduation-cap')

    def test_articles_icon_is_file_text(self):
        link = CuratedLink(category='articles')
        self.assertEqual(link.category_icon_name, 'file-text')

    def test_courses_icon_changed_to_book_open(self):
        """Issue #524: `courses` icon changes so it does not collide with
        the new `workshops` icon (`graduation-cap`)."""
        link = CuratedLink(category='courses')
        self.assertEqual(link.category_icon_name, 'book-open')


class ResourcesSectionOrderIssue524Test(TestCase):
    """`/resources` renders sections in the order
    Workshops, Courses, Articles, Other."""

    def setUp(self):
        self.client = Client()
        CuratedLink.objects.create(
            item_id='ws-1', title='WS Card',
            url='https://example.com/ws', category='workshops',
            sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='co-1', title='CO Card',
            url='https://example.com/co', category='courses',
            sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='ar-1', title='AR Card',
            url='https://example.com/ar', category='articles',
            sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='ot-1', title='OT Card',
            url='https://example.com/ot', category='other',
            sort_order=1, published=True,
        )

    def test_section_keys_in_canonical_order(self):
        response = self.client.get('/resources')
        keys = [g['key'] for g in response.context['grouped_categories']]
        self.assertEqual(keys, ['workshops', 'courses', 'articles', 'other'])

    def test_section_headings_render_in_order(self):
        response = self.client.get('/resources')
        content = response.content.decode()
        # Match the rendered section <h2> exactly to avoid colliding
        # with header/footer nav links to /workshops, etc.
        h2_class = 'class="text-xl font-semibold text-foreground"'
        ws_pos = content.index(f'<h2 {h2_class}>Workshops</h2>')
        co_pos = content.index(f'<h2 {h2_class}>Courses</h2>')
        ar_pos = content.index(f'<h2 {h2_class}>Articles</h2>')
        ot_pos = content.index(f'<h2 {h2_class}>Other</h2>')
        self.assertLess(ws_pos, co_pos)
        self.assertLess(co_pos, ar_pos)
        self.assertLess(ar_pos, ot_pos)

    def test_no_tools_or_models_heading_rendered(self):
        response = self.client.get('/resources')
        content = response.content.decode()
        h2_class = 'class="text-xl font-semibold text-foreground"'
        self.assertNotIn(f'<h2 {h2_class}>Tools</h2>', content)
        self.assertNotIn(f'<h2 {h2_class}>Models</h2>', content)


class ResourcesLegacyFoldIntoOtherIssue524Test(TestCase):
    """Existing `tools` and `models` rows render in the Other section."""

    def setUp(self):
        self.client = Client()
        CuratedLink.objects.create(
            item_id='lg-tool', title='ripgrep',
            url='https://example.com/rg', category='tools',
            sort_order=1, published=True,
        )
        CuratedLink.objects.create(
            item_id='lg-model', title='Llama 3',
            url='https://example.com/llama', category='models',
            sort_order=2, published=True,
        )
        CuratedLink.objects.create(
            item_id='lg-other', title='Common Crawl',
            url='https://example.com/cc', category='other',
            sort_order=3, published=True,
        )

    def test_other_section_contains_all_three_links(self):
        response = self.client.get('/resources')
        grouped = response.context['grouped_categories']
        other_section = next(g for g in grouped if g['key'] == 'other')
        titles = [a['link'].title for a in other_section['links']]
        self.assertIn('ripgrep', titles)
        self.assertIn('Llama 3', titles)
        self.assertIn('Common Crawl', titles)

    def test_only_other_section_present(self):
        response = self.client.get('/resources')
        keys = [g['key'] for g in response.context['grouped_categories']]
        self.assertEqual(keys, ['other'])

    def test_legacy_links_not_outside_other(self):
        """Issue #524: the legacy rows must not slip into other sections."""
        response = self.client.get('/resources')
        grouped = response.context['grouped_categories']
        # No section other than `other` should contain these titles.
        for section in grouped:
            if section['key'] == 'other':
                continue
            titles = [a['link'].title for a in section['links']]
            self.assertNotIn('ripgrep', titles)
            self.assertNotIn('Llama 3', titles)


class ResourcesEmptySectionsHiddenIssue524Test(TestCase):
    """Empty categories do not render a section heading."""

    def test_only_courses_renders_when_only_courses_exist(self):
        CuratedLink.objects.create(
            item_id='solo-course', title='Solo Course',
            url='https://example.com/solo', category='courses',
            sort_order=1, published=True,
        )
        response = self.client.get('/resources')
        keys = [g['key'] for g in response.context['grouped_categories']]
        self.assertEqual(keys, ['courses'])
        content = response.content.decode()
        h2_class = 'class="text-xl font-semibold text-foreground"'
        self.assertIn(f'<h2 {h2_class}>Courses</h2>', content)
        self.assertNotIn(f'<h2 {h2_class}>Workshops</h2>', content)
        self.assertNotIn(f'<h2 {h2_class}>Articles</h2>', content)
        self.assertNotIn(f'<h2 {h2_class}>Other</h2>', content)


class ResourcesHeadingCopyIssue524Test(TestCase):
    """Page header copy reflects the new grouping."""

    def setUp(self):
        self.client = Client()
        CuratedLink.objects.create(
            item_id='copy-link', title='Copy Link',
            url='https://example.com/copy', category='workshops',
            sort_order=1, published=True,
        )

    def test_h1_no_longer_says_tools_models_and_courses(self):
        response = self.client.get('/resources')
        self.assertNotContains(response, 'Tools, Models & Courses')

    def test_h1_uses_new_copy(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Workshops, Courses & More')

    def test_intro_mentions_workshops_and_articles(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'workshops')
        self.assertContains(response, 'articles')


class ResourcesWorkshopBadgeIconIssue524Test(TestCase):
    """A `category='workshops'` card shows the `Workshops` badge with
    the `graduation-cap` icon. Same for `articles`/`file-text`."""

    def test_workshop_card_renders_with_graduation_cap_icon(self):
        CuratedLink.objects.create(
            item_id='ws-badge', title='Agent Eval Workshop',
            description='Hands-on agent evaluation.',
            url='https://example.com/agent-eval',
            category='workshops', sort_order=1, published=True,
        )
        response = self.client.get('/resources')
        grouped = response.context['grouped_categories']
        ws_section = next(g for g in grouped if g['key'] == 'workshops')
        self.assertEqual(ws_section['icon'], 'graduation-cap')
        self.assertEqual(ws_section['label'], 'Workshops')

    def test_article_card_renders_with_file_text_icon(self):
        CuratedLink.objects.create(
            item_id='ar-badge', title='RAG Lessons',
            description='Why RAG pipelines lie.',
            url='https://example.com/rag',
            category='articles', sort_order=1, published=True,
        )
        response = self.client.get('/resources')
        grouped = response.context['grouped_categories']
        ar_section = next(g for g in grouped if g['key'] == 'articles')
        self.assertEqual(ar_section['icon'], 'file-text')
        self.assertEqual(ar_section['label'], 'Articles')
