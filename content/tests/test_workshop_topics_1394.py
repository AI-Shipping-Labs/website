"""Curated workshop-topic catalog coverage for issue #1394."""

from datetime import date
from pathlib import Path

from django.test import TestCase, tag

from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import Workshop

CATALOG_URL = '/workshops/catalog'


def _workshop(slug, title, tags=None, *, status='published', level=LEVEL_OPEN,
              skill_level='', core_tools=None, date_value=date(2026, 8, 11)):
    return Workshop.objects.create(
        slug=slug,
        title=title,
        status=status,
        date=date_value,
        tags=tags or [],
        landing_required_level=LEVEL_OPEN,
        pages_required_level=level,
        recording_required_level=level,
        skill_level=skill_level,
        core_tools=core_tools or [],
    )


def _card_html(response, slug):
    marker = f'data-workshop-slug="{slug}"'
    return response.content.decode().split(marker, 1)[1].split('</article>', 1)[0]


@tag('core')
class WorkshopCuratedTopicCatalogTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.agent_direct = _workshop(
            'agent-direct', 'Agent Direct', ['ai-agents', 'guardrails'],
            date_value=date(2026, 8, 11),
        )
        cls.agent_function = _workshop(
            'agent-function', 'Function Calling',
            ['function-calling', 'personal-brand'],
            date_value=date(2026, 8, 10),
        )
        cls.rag = _workshop(
            'rag-search', 'Search Workshop', ['rag', 'elasticsearch'],
            date_value=date(2026, 8, 9),
        )
        cls.production = _workshop(
            'production-app', 'Production App', ['python', 'django'],
            skill_level='intermediate', core_tools=['Python'],
            date_value=date(2026, 8, 8),
        )
        cls.career = _workshop(
            'career', 'Career Workshop', ['career', 'linkedin'],
            level=LEVEL_MAIN, date_value=date(2026, 8, 7),
        )
        cls.unmapped = _workshop(
            'unmapped', 'Unmapped Workshop', ['brand-new-subject'],
            date_value=date(2026, 8, 6),
        )
        cls.untagged = _workshop(
            'untagged', 'Untagged Workshop', [],
            date_value=date(2026, 8, 5),
        )
        cls.draft = _workshop(
            'draft-coding', 'Draft Coding Workshop', ['coding-assistants'],
            status='draft', date_value=date(2026, 8, 4),
        )

    def test_catalog_renders_only_represented_curated_topics_in_order(self):
        response = self.client.get(CATALOG_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [topic['slug'] for topic in response.context['topic_filters']],
            ['ai-agents', 'rag-search', 'production-apps', 'career'],
        )
        self.assertContains(response, 'data-testid="workshop-topic-all"')
        self.assertContains(response, 'AI Agents')
        self.assertContains(response, 'RAG &amp; Search')
        self.assertContains(response, 'Production &amp; Apps')
        self.assertContains(response, 'Career')
        self.assertNotContains(response, 'data-testid="workshop-topic-coding-with-ai"')
        self.assertNotContains(response, 'Draft Coding Workshop')
        self.assertContains(response, 'data-testid="workshop-topic-filter"')

    def test_topic_filter_uses_or_semantics_and_one_active_selection(self):
        response = self.client.get(f'{CATALOG_URL}?topic=ai-agents')

        self.assertEqual(response.context['selected_topic'], 'ai-agents')
        self.assertContains(response, 'Agent Direct')
        self.assertContains(response, 'Function Calling')
        self.assertNotContains(response, 'Search Workshop')
        selected = response.content.decode().split(
            'data-testid="workshop-topic-ai-agents"', 1,
        )[1].split('>', 1)[0]
        self.assertIn('aria-current="page"', selected)
        self.assertContains(response, f'href="{CATALOG_URL}?topic=career"')
        self.assertNotContains(response, 'topic=ai-agents&amp;topic=career')

    def test_all_and_unknown_topic_keep_every_published_workshop_discoverable(self):
        for url in (CATALOG_URL, f'{CATALOG_URL}?topic=unknown'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.context['selected_topic'], '')
                self.assertContains(response, 'Agent Direct')
                self.assertContains(response, 'Career Workshop')
                self.assertContains(response, 'Unmapped Workshop')
                self.assertContains(response, 'Untagged Workshop')
                self.assertNotContains(response, 'Draft Coding Workshop')
                all_pill = response.content.decode().split(
                    'data-testid="workshop-topic-all"', 1,
                )[1].split('>', 1)[0]
                self.assertIn('aria-current="page"', all_pill)

    def test_retired_parameters_are_noops_and_render_no_hidden_filter_ui(self):
        response = self.client.get(
            f'{CATALOG_URL}?access=paid&skill_level=expert&tool=Python',
        )

        self.assertContains(response, 'Agent Direct')
        self.assertContains(response, 'Career Workshop')
        self.assertContains(response, 'Untagged Workshop')
        self.assertNotContains(response, 'workshop-access-filter')
        self.assertNotContains(response, 'workshop-skill-filter')
        self.assertNotContains(response, 'workshop-facet-topic')
        self.assertNotContains(response, 'workshop-facet-technology')
        self.assertNotContains(response, 'workshop-active-filters')

    def test_legacy_tag_links_keep_bounded_and_semantics_without_controls(self):
        response = self.client.get(
            f'{CATALOG_URL}?tag=function-calling&tag=personal-brand',
        )

        self.assertContains(response, 'Function Calling')
        self.assertNotContains(response, 'Agent Direct')
        self.assertNotContains(response, 'data-testid="workshop-active-tag"')
        self.assertNotContains(response, 'function-calling</a>')

        empty = self.client.get(f'{CATALOG_URL}?tag=no-longer-used')
        self.assertContains(empty, 'data-empty-kind="filter"')
        self.assertContains(empty, 'No workshops found')
        self.assertContains(empty, 'View all workshops')
        self.assertContains(empty, f'href="{CATALOG_URL}"')

    def test_cards_use_one_primary_topic_and_never_render_raw_tags(self):
        response = self.client.get(CATALOG_URL)

        agent_card = _card_html(response, 'agent-function')
        self.assertEqual(agent_card.count('data-testid="workshop-card-topic"'), 1)
        self.assertIn('AI Agents', agent_card)
        self.assertNotIn('function-calling', agent_card)
        self.assertNotIn('personal-brand', agent_card)
        self.assertIn('data-testid="workshop-free-badge"', agent_card)
        production_card = _card_html(response, 'production-app')
        self.assertIn('Production &amp; Apps', production_card)
        self.assertIn('data-testid="workshop-skill-badge"', production_card)
        self.assertLess(
            production_card.index('data-testid="workshop-card-metadata"'),
            production_card.index('data-testid="workshop-skill-badge"'),
        )
        career_card = _card_html(response, 'career')
        self.assertIn('data-testid="workshop-tier-badge"', career_card)
        self.assertNotIn('linkedin', career_card)
        for slug in ('unmapped', 'untagged'):
            self.assertNotIn(
                'data-testid="workshop-card-topic"',
                _card_html(response, slug),
            )

    def test_landing_preview_uses_same_topic_cards_without_filter_controls(self):
        response = self.client.get('/workshops')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="featured-workshop-card"')
        self.assertContains(response, 'data-testid="workshop-card-topic"')
        self.assertContains(response, 'AI Agents')
        self.assertNotContains(response, 'data-testid="workshop-topic-filter"')
        self.assertContains(response, 'data-testid="view-all-workshops-preview-cta"')
        self.assertContains(response, f'href="{CATALOG_URL}"')
        template = Path('templates/content/_workshops_catalog.html').read_text(
            encoding='utf-8',
        )
        self.assertIn(
            "{% button_classes 'secondary' extra='mt-6' %}",
            template,
        )

    def test_catalog_uses_reader_width_and_editorial_rows(self):
        response = self.client.get(CATALOG_URL)

        self.assertContains(response, 'mx-auto max-w-3xl')
        self.assertContains(response, 'data-testid="workshops-list"')
        self.assertNotContains(response, 'lg:grid-cols-3')
        card = _card_html(response, 'agent-direct')
        self.assertContains(response, 'border-b border-border/70')
        self.assertIn('py-6 sm:flex-row', card)
        self.assertNotIn('rounded-lg border border-border bg-card', card)

    def test_landing_preview_keeps_three_card_grid_at_landing_width(self):
        response = self.client.get('/workshops')

        self.assertContains(response, 'data-testid="workshops-grid"')
        self.assertContains(response, 'grid gap-6 sm:grid-cols-2 lg:grid-cols-3')
        self.assertContains(response, 'mx-auto max-w-5xl')
