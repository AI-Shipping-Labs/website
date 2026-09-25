"""Render-time relative-.md link resolution (#1815).

``rewrite_relative_md_links`` is a pure function of the markdown text and
the published slug set: a resolvable stem becomes ``/topics/<slug>/``, the
hub stem ``index`` becomes ``/topics/`` (the slug is reserved; there is no
``/topics/index/`` route), and a stem with no published page degrades to
its plain link text with no anchor. ``TopicPage.save`` renders through the
live published set, so stored ``body_html`` never carries an internal href
that 404s.
"""

from django.test import SimpleTestCase, TestCase

from topics.models import STATUS_DRAFT, TopicPage
from topics.rendering import rewrite_relative_md_links


class RewriteRelativeMdLinksPureTest(SimpleTestCase):
    """The rewriter is pure: no database access anywhere in the path."""

    def test_resolvable_stem_becomes_topic_url(self):
        self.assertEqual(
            rewrite_relative_md_links('[RAG](rag.md)', {'rag'}),
            '[RAG](/topics/rag/)',
        )

    def test_unresolvable_stem_degrades_to_plain_text(self):
        markdown = 'Read [Ghost Topic](ghost-topic.md) next.'
        self.assertEqual(
            rewrite_relative_md_links(markdown, {'rag'}),
            'Read Ghost Topic next.',
        )

    def test_none_slug_set_keeps_unconditional_rewrite(self):
        self.assertEqual(
            rewrite_relative_md_links('[RAG](rag.md)'),
            '[RAG](/topics/rag/)',
        )

    def test_hub_stem_links_to_topics_root(self):
        self.assertEqual(
            rewrite_relative_md_links('[Back to the hub](index.md)', {'index'}),
            '[Back to the hub](/topics/)',
        )

    def test_hub_stem_without_published_hub_is_plain_text(self):
        self.assertEqual(
            rewrite_relative_md_links('[Back to the hub](index.md)', {'rag'}),
            'Back to the hub',
        )

    def test_targets_that_never_named_a_sibling_md_pass_through(self):
        cases = (
            '[Course](https://aishippinglabs.com/courses/aihero)',
            '[Blog](/blog)',
            '[Start here](#start-here)',
            '[Mail us](mailto:hi@example.com)',
            '[Notes](notes.txt)',
        )
        for markdown in cases:
            with self.subTest(markdown=markdown):
                self.assertEqual(
                    rewrite_relative_md_links(markdown, {'rag'}),
                    markdown,
                )

    def test_nested_path_uses_the_last_stem(self):
        self.assertEqual(
            rewrite_relative_md_links('[Page](sub/page.md)', {'page'}),
            '[Page](/topics/page/)',
        )

    def test_image_bang_is_preserved_for_resolvable_target(self):
        self.assertEqual(
            rewrite_relative_md_links('![Diagram](diagram.md)', {'diagram'}),
            '![Diagram](/topics/diagram/)',
        )

    def test_image_with_unresolvable_target_degrades_to_alt_text(self):
        self.assertEqual(
            rewrite_relative_md_links('![Diagram](missing.md)', {'rag'}),
            'Diagram',
        )


class TopicPageSaveSlugResolutionTest(TestCase):
    """``TopicPage.save`` renders against the live published slug set."""

    def test_link_to_published_page_is_stored_as_href(self):
        TopicPage.objects.create(slug='rag', title='RAG', body='The pipeline.')
        page = TopicPage.objects.create(
            slug='index',
            title='AISL Wiki',
            body='Start with [RAG](rag.md), then go deeper.',
        )
        self.assertIn('href="/topics/rag/"', page.body_html)

    def test_link_to_draft_page_is_plain_text(self):
        TopicPage.objects.create(
            slug='ghost',
            title='Ghost',
            body='A draft page.',
            status=STATUS_DRAFT,
        )
        page = TopicPage.objects.create(
            slug='index',
            title='AISL Wiki',
            body='See [Ghost](ghost.md) later.',
        )
        self.assertNotIn('<a ', page.body_html)
        self.assertNotIn('href="/topics/ghost/"', page.body_html)
        self.assertIn('Ghost', page.body_html)

    def test_link_to_missing_page_is_plain_text(self):
        page = TopicPage.objects.create(
            slug='index',
            title='AISL Wiki',
            body='See [Missing Page](missing-stem.md) later.',
        )
        self.assertNotIn('<a ', page.body_html)
        self.assertNotIn('missing-stem', page.body_html)
        self.assertIn('Missing Page', page.body_html)

    def test_self_link_before_first_save_renders_plain_text(self):
        # Conservative render: on the INSERT the row is not yet in the
        # published set, so a self-link degrades to text rather than
        # guessing. The post-sync re-render pass (topics.sync) re-renders
        # every page against the complete set once the run finishes, so
        # served pages never keep this mid-run render.
        page = TopicPage.objects.create(
            slug='rag',
            title='RAG',
            body='Back to [RAG](rag.md) from here.',
        )
        self.assertEqual(page.body_html, '<p>Back to RAG from here.</p>')

    def test_self_link_resolves_once_the_row_is_published(self):
        page = TopicPage.objects.create(
            slug='rag',
            title='RAG',
            body='Back to [RAG](rag.md) from here.',
        )
        page.save(update_fields=['body_html'])
        self.assertIn('href="/topics/rag/"', page.body_html)

    def test_draft_page_still_links_published_targets(self):
        TopicPage.objects.create(slug='rag', title='RAG', body='The pipeline.')
        page = TopicPage.objects.create(
            slug='ghost',
            title='Ghost',
            body='Compare with [RAG](rag.md).',
            status=STATUS_DRAFT,
        )
        self.assertIn('href="/topics/rag/"', page.body_html)

    def test_hub_link_resolves_to_topics_root(self):
        TopicPage.objects.create(slug='index', title='AISL Wiki', body='Hub.')
        page = TopicPage.objects.create(
            slug='rag',
            title='RAG',
            body='Back to [the hub](index.md).',
        )
        self.assertIn('href="/topics/"', page.body_html)
        self.assertNotIn('href="/topics/index/"', page.body_html)
