"""Open-access rendering coverage for the /topics/ surfaces (#1804).

Every viewer -- anonymous, Free, paid, staff -- gets the full hub and
topic bodies plus the conversion band; the gated render (teaser, blur,
gated card, sign-in link) is gone from the surface entirely. Drafts 404
and stay out of the hub grid, and the SEO contract renders on detail
pages.
"""

import re
import xml.etree.ElementTree as ET

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.templatetags.accounts_extras import button_classes
from content.access import LEVEL_OPEN
from tests.fixtures import TierSetupMixin, set_membership
from topics.models import STATUS_DRAFT, TopicPage

HUB_BODY_SENTENCE = (
    'The wiki turns the AISL material into topic guides, and this '
    'sentence lives only in the stored hub body, so finding it verbatim '
    'proves the full body reached the response.'
)
RAG_BODY_SENTENCE = (
    'RAG retrieves chunks the model never memorized, and this sentence '
    'lives only in the stored page body, so finding it verbatim proves '
    'the full guide reached the response.'
)
VECTOR_BODY_SENTENCE = (
    'Vector search goes from TF-IDF to embeddings, from first '
    'principles to SQLite and Turso.'
)

RAG_RELATED = ['agents', 'vector-search', 'does-not-exist']

# Testids of the retired gated render: none of them may appear on any
# topics response for any viewer.
GATE_TESTIDS = (
    'topics-gated-card',
    'topics-gated-cta',
    'topic-teaser',
    'topic-teaser-blur',
    'topics-signin-link',
)

SITEMAP_NS = '{http://www.sitemaps.org/schemas/sitemap/0.9}'


def _create_topic(slug, title, body, *, summary='A summary.', related=None,
                  status='published'):
    return TopicPage.objects.create(
        slug=slug,
        title=title,
        summary=summary,
        body=body,
        related=related or [],
        status=status,
    )


def _anchor_tag(content, testid):
    """The one anchor whose data-testid is `testid`, or a failed assert."""
    match = re.search(rf'<a [^>]*data-testid="{testid}"[^>]*>', content)
    assert match, f'anchor with data-testid={testid!r} missing'
    return match.group(0)


def _assert_no_gate_render(test, content):
    for testid in GATE_TESTIDS:
        with test.subTest(gate_testid=testid):
            test.assertNotIn(f'data-testid="{testid}"', content)


def _sitemap_paths_and_lastmods(response):
    """Parse /sitemap.xml into {path: lastmod-text}."""
    root = ET.fromstring(response.content)
    entries = {}
    for url in root.findall(f'{SITEMAP_NS}url'):
        loc = url.findtext(f'{SITEMAP_NS}loc') or ''
        path = re.sub(r'^https?://[^/]+', '', loc)
        entries[path] = url.findtext(f'{SITEMAP_NS}lastmod') or ''
    return entries


class _TieredUsersMixin(TierSetupMixin):
    """Standard tiers plus one member per level used by the matrix."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.free_user = User.objects.create_user(
            email='free@test.com', password='testpass',
        )
        set_membership(cls.free_user, tier=cls.free_tier)
        cls.basic_user = User.objects.create_user(
            email='basic@test.com', password='testpass',
        )
        set_membership(cls.basic_user, tier=cls.basic_tier)
        cls.main_user = User.objects.create_user(
            email='main@test.com', password='testpass',
        )
        set_membership(cls.main_user, tier=cls.main_tier)


class _TopicsFixtureMixin(_TieredUsersMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.index = _create_topic(
            'index',
            'AISL Wiki',
            HUB_BODY_SENTENCE + '\n\n## Start here\n\n- [RAG](rag.md)\n',
            summary='Topic guides built from every AISL course, workshop, and article.',
        )
        cls.rag = _create_topic(
            'rag',
            'RAG',
            RAG_BODY_SENTENCE,
            summary='Retrieval-augmented generation, summarized.',
            related=list(RAG_RELATED),
        )
        _create_topic(
            'agents',
            'Agents',
            'Agents are the loop.',
            summary='The agent loop, summarized.',
            related=['rag'],
        )
        _create_topic(
            'vector-search',
            'Vector Search',
            VECTOR_BODY_SENTENCE,
            summary='Embeddings and similarity, summarized.',
        )
        cls.draft = _create_topic(
            'evaluation',
            'Evaluation',
            'Evaluate the system.',
            summary='Draft evaluation summary.',
            status=STATUS_DRAFT,
        )


class TopicsHubOpenAccessTest(_TopicsFixtureMixin, TestCase):
    """The hub renders its full body for every viewer, no gate anywhere."""

    def test_anonymous_gets_full_hub_body(self):
        response = self.client.get('/topics/')
        content = response.content.decode()
        # assertContains pins the 200 alongside the contract itself.
        self.assertContains(response, 'data-testid="topics-hub-body"')
        self.assertContains(response, HUB_BODY_SENTENCE)
        self.assertContains(response, self.index.body_html)
        _assert_no_gate_render(self, content)

    def test_free_member_gets_identical_full_hub_body(self):
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/topics/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topics-hub-body"')
        self.assertContains(response, HUB_BODY_SENTENCE)
        _assert_no_gate_render(self, content)
        # The funnel stays visible to signed-in free members too.
        self.assertContains(response, 'data-testid="topics-conversion"')

    def test_paid_members_get_full_hub_body(self):
        for email in ('basic@test.com', 'main@test.com'):
            with self.subTest(email=email):
                self.client.login(email=email, password='testpass')
                response = self.client.get('/topics/')
                content = response.content.decode()
                self.assertContains(response, 'data-testid="topics-hub-body"')
                self.assertContains(response, HUB_BODY_SENTENCE)
                _assert_no_gate_render(self, content)
                self.client.logout()

    def test_staff_member_reads_hub(self):
        User = get_user_model()
        staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email=staff.email, password='testpass')
        response = self.client.get('/topics/')
        self.assertContains(response, 'data-testid="topics-hub-body"')

    def test_hub_grid_lists_published_pages_with_title_and_summary(self):
        response = self.client.get('/topics/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topics-grid"')
        self.assertEqual(content.count('data-testid="topic-card"'), 3)
        for title in ('RAG', 'Agents', 'Vector Search'):
            with self.subTest(title=title):
                self.assertIsNotNone(
                    re.search(rf'>\s*{title}\s*</h2>', content),
                    f'grid card title {title!r} missing',
                )
        for summary in (
            'Retrieval-augmented generation, summarized.',
            'The agent loop, summarized.',
            'Embeddings and similarity, summarized.',
        ):
            with self.subTest(summary=summary):
                self.assertIn(summary, content)
        # The draft page is absent from the grid.
        self.assertNotContains(response, 'Draft evaluation summary.')
        self.assertNotContains(response, 'href="/topics/evaluation/"')

    def test_hub_without_index_page_is_404(self):
        TopicPage.objects.filter(slug='index').delete()
        response = self.client.get('/topics/')
        self.assertEqual(response.status_code, 404)


@pytest.mark.visual_regression
class HubConversionSectionTest(_TopicsFixtureMixin, TestCase):
    """The hub funnel band sits below the grid with the two open CTAs."""

    def test_conversion_section_renders_below_grid(self):
        response = self.client.get('/topics/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topics-conversion"')
        # The band comes after the All topics grid in document order.
        self.assertLess(
            content.index('data-testid="topics-grid"'),
            content.index('data-testid="topics-conversion"'),
        )

    def test_membership_cta_is_primary_large(self):
        content = self.client.get('/topics/').content.decode()
        tag = _anchor_tag(content, 'topics-cta-membership')
        self.assertIn('href="/membership"', tag)
        self.assertIn('View membership plans', content)
        # size='lg' primary chrome from the button_classes owner.
        self.assertIn(button_classes('primary', size='lg'), tag)

    def test_workshops_cta_is_secondary_medium(self):
        content = self.client.get('/topics/').content.decode()
        tag = _anchor_tag(content, 'topics-cta-workshops')
        self.assertIn('href="/workshops"', tag)
        self.assertIn('Browse workshops', content)
        # size='md' secondary chrome from the button_classes owner.
        self.assertIn(button_classes('secondary', size='md'), tag)


class TopicPageOpenAccessTest(_TopicsFixtureMixin, TestCase):
    """Topic pages render their full body for every viewer."""

    def test_anonymous_reads_full_topic_body(self):
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topic-body"')
        self.assertContains(response, RAG_BODY_SENTENCE)
        _assert_no_gate_render(self, content)

    def test_free_member_reads_full_topic_body(self):
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topic-body"')
        self.assertContains(response, RAG_BODY_SENTENCE)
        _assert_no_gate_render(self, content)
        self.assertContains(response, 'data-testid="topics-conversion"')

    def test_paid_members_read_full_topic_body(self):
        for email in ('basic@test.com', 'main@test.com'):
            with self.subTest(email=email):
                self.client.login(email=email, password='testpass')
                response = self.client.get('/topics/rag/')
                content = response.content.decode()
                self.assertContains(response, RAG_BODY_SENTENCE)
                _assert_no_gate_render(self, content)
                self.client.logout()


@pytest.mark.visual_regression
class TopicConversionSectionTest(_TopicsFixtureMixin, TestCase):
    """Every published topic page funnels to membership and the Buildcamp."""

    def test_conversion_section_renders_after_body_and_related(self):
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topics-conversion"')
        self.assertLess(
            content.index('data-testid="topic-body"'),
            content.index('data-testid="topic-related"'),
        )
        self.assertLess(
            content.index('data-testid="topic-related"'),
            content.index('data-testid="topics-conversion"'),
        )

    def test_membership_cta_is_primary_large(self):
        content = self.client.get('/topics/rag/').content.decode()
        tag = _anchor_tag(content, 'topics-cta-membership')
        self.assertIn('href="/membership"', tag)
        self.assertIn(button_classes('primary', size='lg'), tag)

    def test_buildcamp_cta_is_secondary_medium(self):
        content = self.client.get('/topics/rag/').content.decode()
        tag = _anchor_tag(content, 'topics-cta-buildcamp')
        self.assertIn('href="/courses/ai-buildcamp"', tag)
        self.assertIn('Explore the AI Buildcamp', content)
        self.assertIn(button_classes('secondary', size='md'), tag)


class TopicRelatedLinksTest(_TopicsFixtureMixin, TestCase):
    def test_related_links_resolve_for_anonymous(self):
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topic-related"')
        self.assertEqual(content.count('data-testid="topic-related-link"'), 2)
        self.assertContains(response, 'href="/topics/agents/"')
        self.assertContains(response, 'href="/topics/vector-search/"')

    def test_dangling_related_slug_skipped(self):
        response = self.client.get('/topics/rag/')
        self.assertNotContains(response, 'href="/topics/does-not-exist/"')

    def test_related_section_absent_when_nothing_resolves(self):
        response = self.client.get('/topics/vector-search/')
        self.assertNotContains(response, 'data-testid="topic-related"')


class TopicDraftAndReservedSlugTest(_TopicsFixtureMixin, TestCase):
    def test_draft_page_404s_for_anonymous(self):
        response = self.client.get('/topics/evaluation/')
        self.assertEqual(response.status_code, 404)

    def test_draft_page_404s_for_members(self):
        for email in ('free@test.com', 'basic@test.com'):
            with self.subTest(email=email):
                self.client.login(email=email, password='testpass')
                response = self.client.get('/topics/evaluation/')
                self.assertEqual(response.status_code, 404)
                self.client.logout()

    def test_hub_slug_has_no_detail_page(self):
        response = self.client.get('/topics/index/')
        self.assertEqual(response.status_code, 404)

    def test_unknown_slug_404s(self):
        response = self.client.get('/topics/nope/')
        self.assertEqual(response.status_code, 404)


class TopicSeoTagsTest(_TopicsFixtureMixin, TestCase):
    """Detail pages carry the canonical + OG/Twitter contract (#1804)."""

    def test_detail_emits_canonical_and_social_tags(self):
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        canonical = re.search(
            r'<link rel="canonical" href="[^"]*">$', content, re.M,
        )
        self.assertIsNotNone(canonical, 'canonical link missing')
        # page_seo_tags normalizes routes to no-trailing-slash form.
        self.assertTrue(
            canonical.group(0).endswith('/topics/rag">'),
            f'canonical points at the wrong path: {canonical.group(0)}',
        )
        self.assertIn(
            '<meta property="og:title" content="RAG | AI Shipping Labs">',
            content,
        )
        self.assertIn(
            '<meta property="og:description" '
            'content="Retrieval-augmented generation, summarized.">',
            content,
        )
        self.assertIn(
            '<meta name="twitter:card" content="summary_large_image">',
            content,
        )

    def test_detail_title_matches_document_title(self):
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertIn('<title>RAG | AI Shipping Labs</title>', content)
        self.assertIn(
            '<meta property="og:title" content="RAG | AI Shipping Labs">',
            content,
        )

    def test_detail_without_summary_falls_back_to_title_description(self):
        _create_topic('bare', 'Bare Page', 'Body only.', summary='')
        content = self.client.get('/topics/bare/').content.decode()
        self.assertIn(
            '<meta property="og:description" '
            'content="Bare Page - an AI Shipping Labs member topic guide.">',
            content,
        )

    def test_hub_keeps_canonical_and_social_tags(self):
        response = self.client.get('/topics/')
        content = response.content.decode()
        self.assertIn(
            '<meta property="og:title" content="Topics | AI Shipping Labs">',
            content,
        )
        self.assertTrue(
            re.search(r'<link rel="canonical" href="[^"]*">$', content, re.M),
            'hub canonical link missing',
        )


class TopicsSitemapInclusionTest(_TopicsFixtureMixin, TestCase):
    """The open topics wiki is a sitemap member (#1804)."""

    def test_sitemap_lists_hub_and_published_pages(self):
        response = self.client.get('/sitemap.xml')
        root = ET.fromstring(response.content)
        self.assertEqual(root.tag, f'{SITEMAP_NS}urlset')
        entries = _sitemap_paths_and_lastmods(response)
        self.assertIn('/topics/', entries)
        self.assertIn('/topics/rag/', entries)
        self.assertIn('/topics/agents/', entries)
        self.assertIn('/topics/vector-search/', entries)

    def test_sitemap_lastmod_comes_from_updated_at(self):
        response = self.client.get('/sitemap.xml')
        entries = _sitemap_paths_and_lastmods(response)
        expected = timezone.localdate(self.rag.updated_at).isoformat()
        self.assertTrue(
            entries['/topics/rag/'].startswith(expected),
            f'lastmod {entries["/topics/rag/"]!r} does not match '
            f'updated_at date {expected!r}',
        )

    def test_sitemap_excludes_draft_pages(self):
        response = self.client.get('/sitemap.xml')
        entries = _sitemap_paths_and_lastmods(response)
        self.assertNotIn('/topics/evaluation/', entries)


class TopicModelRenderingTest(TestCase):
    """Model-level behavior of the body pipeline and related resolution."""

    def test_new_pages_default_to_open(self):
        page = _create_topic('defaults', 'Defaults', 'Body.')
        self.assertEqual(page.required_level, LEVEL_OPEN)

    def test_relative_md_links_rewritten_in_body_html(self):
        page = _create_topic(
            'index',
            'AISL Wiki',
            '- [RAG](rag.md)\n- [Course](https://aishippinglabs.com/courses/aihero)\n',
        )
        page = TopicPage.objects.get(slug='index')
        self.assertIn('href="/topics/rag/"', page.body_html)
        self.assertIn(
            'href="https://aishippinglabs.com/courses/aihero"',
            page.body_html,
        )
        self.assertNotIn('rag.md', page.body_html)

    def test_resolved_related_skips_draft_and_unknown(self):
        published = _create_topic('agents', 'Agents', 'The loop.')
        _create_topic('ghost', 'Ghost', 'A draft page.', status=STATUS_DRAFT)
        page = _create_topic(
            'rag', 'RAG', 'The pipeline.',
            related=['agents', 'ghost', 'missing', 'rag'],
        )
        resolved = page.resolved_related()
        self.assertEqual([topic.slug for topic in resolved], ['agents'])
        self.assertEqual(resolved[0], published)
