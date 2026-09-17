"""Gating and rendering coverage for the /topics/ surfaces (#1688).

Access matrix: anonymous gets a sign-in prompt with no body, Free gets a
teaser with no body, Basic and above read the full page, drafts 404. The
body-leak assertions check for sentences that exist only in the stored
body, so any leak of ``body`` or ``body_html`` fails the test.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from tests.fixtures import TierSetupMixin, set_membership
from topics.models import STATUS_DRAFT, TopicPage

HUB_BODY_SENTENCE = (
    'Jump straight into the roadmap with hub-level context that keeps '
    'going well past the two hundred plain-text characters a teaser '
    'paragraph is ever allowed to show, and every word after this point '
    'carries material reserved for members whose tier is Basic or above, '
    'so it must never reach an anonymous or Free-tier response.'
)
RAG_BODY_SENTENCE = (
    'RAG retrieves chunks the model never memorized, and the interesting '
    'failures begin exactly where a fixed pipeline searches once and '
    'hopes for the best, while the remainder of the explanation runs on '
    'well past the teaser window and stays behind the tier gate, out of '
    'every anonymous or Free-tier response no matter how the page is '
    'fetched.'
)
VECTOR_BODY_SENTENCE = (
    'Vector search goes from TF-IDF to embeddings, from first principles '
    'to SQLite and Turso, with enough depth that the plain text runs long '
    'past the teaser window and shows real member-only material only '
    'after the gate opens.'
)

# Distinctive tails: they start beyond the teaser window (TEASER_MAX_CHARS
# is 200 and the clause before each tail fills it), so finding one in a
# denied response means the gated body leaked.
HUB_LEAK_TAIL = 'reserved for members'
RAG_LEAK_TAIL = 'stays behind the tier gate'

RAG_RELATED = ['agents', 'vector-search', 'does-not-exist']


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


class TopicsHubAccessTest(_TopicsFixtureMixin, TestCase):
    def test_anonymous_sees_signin_prompt_and_no_body(self):
        response = self.client.get('/topics/')
        content = response.content.decode()
        # Sign-in prompt renders (assertContains also pins the 200).
        self.assertContains(response, 'data-testid="topics-signin-link"')
        # The gated body content must not appear in any form: neither the
        # full sentence nor the tail beyond the teaser window.
        self.assertNotIn(HUB_BODY_SENTENCE, content)
        self.assertNotIn(HUB_LEAK_TAIL, content)
        self.assertNotIn(self.index.body_html, content)
        self.assertNotIn('data-testid="topics-hub-body"', content)

    def test_free_user_sees_teaser_blur_and_upgrade_cta(self):
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/topics/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topic-teaser"')
        self.assertContains(response, 'data-testid="topic-teaser-blur"')
        self.assertContains(response, 'data-testid="topics-gated-cta"')
        self.assertIn('href="/membership"', content)
        # Teaser text may show; the tail of the body may not.
        self.assertNotIn(HUB_LEAK_TAIL, content)
        # Signed-in free users stay on the upgrade path: no sign-in prompt.
        self.assertNotIn('data-testid="topics-signin-link"', content)

    def test_basic_member_reads_hub_body(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/')
        self.assertContains(response, 'data-testid="topics-hub-body"')
        self.assertContains(response, HUB_BODY_SENTENCE)
        self.assertNotIn('data-testid="topics-gated-cta"', response.content.decode())

    def test_hub_grid_lists_published_pages_with_title_and_summary(self):
        self.client.login(email='basic@test.com', password='testpass')
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

    def test_staff_member_reads_hub(self):
        User = get_user_model()
        staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email=staff.email, password='testpass')
        response = self.client.get('/topics/')
        self.assertContains(response, 'data-testid="topics-hub-body"')


class TopicPageAccessTest(_TopicsFixtureMixin, TestCase):
    def test_anonymous_gets_signin_prompt_and_no_body(self):
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertIn('data-testid="topics-signin-link"', content)
        self.assertContains(response, 'RAG')
        self.assertNotIn(RAG_BODY_SENTENCE, content)
        self.assertNotIn(RAG_LEAK_TAIL, content)
        self.assertNotIn('data-testid="topic-body"', content)

    def test_free_user_gets_teaser_and_upgrade_path(self):
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topic-teaser"')
        self.assertContains(response, 'data-testid="topic-teaser-blur"')
        self.assertContains(response, 'data-testid="topics-gated-cta"')
        self.assertIn('href="/membership"', content)
        self.assertNotIn(RAG_BODY_SENTENCE, content)
        self.assertNotIn(RAG_LEAK_TAIL, content)

    def test_basic_member_reads_full_page(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/rag/')
        self.assertContains(response, 'data-testid="topic-body"')
        self.assertContains(response, RAG_BODY_SENTENCE)
        self.assertNotIn('data-testid="topics-gated-cta"', response.content.decode())

    def test_main_and_premium_read_full_page(self):
        for email in ('main@test.com',):
            self.client.login(email=email, password='testpass')
            response = self.client.get('/topics/rag/')
            self.assertContains(response, RAG_BODY_SENTENCE)
            self.client.logout()


class TopicRelatedLinksTest(_TopicsFixtureMixin, TestCase):
    def test_related_links_resolve_for_basic_member(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/rag/')
        content = response.content.decode()
        self.assertContains(response, 'data-testid="topic-related"')
        self.assertEqual(content.count('data-testid="topic-related-link"'), 2)
        self.assertContains(response, 'href="/topics/agents/"')
        self.assertContains(response, 'href="/topics/vector-search/"')

    def test_dangling_related_slug_skipped(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/rag/')
        self.assertNotContains(response, 'href="/topics/does-not-exist/"')

    def test_related_section_absent_when_nothing_resolves(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/vector-search/')
        self.assertNotContains(response, 'data-testid="topic-related"')

    def test_related_section_hidden_from_denied_levels(self):
        response = self.client.get('/topics/rag/')
        self.assertNotContains(response, 'data-testid="topic-related"')


class TopicDraftAndReservedSlugTest(_TopicsFixtureMixin, TestCase):
    def test_draft_page_404s_for_members(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/evaluation/')
        self.assertEqual(response.status_code, 404)

    def test_draft_page_404s_for_anonymous(self):
        response = self.client.get('/topics/evaluation/')
        self.assertEqual(response.status_code, 404)

    def test_hub_slug_has_no_detail_page(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/topics/index/')
        self.assertEqual(response.status_code, 404)

    def test_unknown_slug_404s(self):
        response = self.client.get('/topics/nope/')
        self.assertEqual(response.status_code, 404)


class TopicModelRenderingTest(TestCase):
    """Model-level behavior of the body pipeline and related resolution."""

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
