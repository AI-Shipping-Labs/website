"""Regression tests for the /projects tag filter UI.

The projects_list view has long computed `all_tags` / `selected_tags` and
supported `?tag=` filtering, but the template rendered no filter control and
the card tag chips were inert spans — the whole feature was unreachable.
These tests pin the template-side wiring:

- the topic pill row renders, with an `All` reset pill and an active state
- selecting a pill narrows the visible cards to matching projects only
- card tag chips are real links to the filtered listing, never nested
  inside the card's own anchor
- with more than 12 tags the remainder moves into an expandable
  disclosure (nothing is silently truncated), which auto-opens when a
  hidden tag is the active filter
- tag pills preserve an active difficulty filter
"""

from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, tag

from content.models import Project


class _TagControlParser(HTMLParser):
    """Collect the tag-filter pills, card tag chips, and the disclosure."""

    def __init__(self):
        super().__init__()
        self.tag_pills = {}
        self.card_tag_links = []
        self.nested_card_tag_links = 0
        self.more_details_attrs = None
        self._anchor_depth = 0

    def handle_starttag(self, tag_name, attrs):
        attrs = dict(attrs)
        testid = attrs.get('data-testid', '')
        if tag_name == 'a':
            if testid.startswith('project-tag-'):
                self.tag_pills[testid] = attrs
            if testid == 'project-card-tag-link':
                self.card_tag_links.append(attrs)
                if self._anchor_depth:
                    self.nested_card_tag_links += 1
            self._anchor_depth += 1
        elif tag_name == 'details' and testid == 'project-tag-more':
            self.more_details_attrs = attrs

    def handle_endtag(self, tag_name):
        if tag_name == 'a' and self._anchor_depth:
            self._anchor_depth -= 1


def _parse(response):
    parser = _TagControlParser()
    parser.feed(response.content.decode())
    return parser


def _query(attrs):
    return parse_qs(urlparse(attrs.get('href', '')).query)


@tag('core')
class ProjectsTagFilterRenderTest(TestCase):
    """The filter control renders and drives real filtering."""

    @classmethod
    def setUpTestData(cls):
        cls.agents_project = Project.objects.create(
            title='Agents Starter',
            slug='agents-starter-filter',
            description='Build a first agent',
            date=date(2026, 7, 1),
            difficulty='beginner',
            tags=['agents', 'python'],
            published=True,
        )
        cls.rag_project = Project.objects.create(
            title='RAG Search Engine',
            slug='rag-search-filter',
            description='Retrieval pipeline project',
            date=date(2026, 7, 2),
            difficulty='advanced',
            tags=['rag'],
            published=True,
        )

    def test_topic_pill_row_renders_with_all_pill_active(self):
        response = self.client.get('/projects')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="project-tag-filter"')

        parser = _parse(response)
        self.assertIn('project-tag-all', parser.tag_pills)
        all_pill = parser.tag_pills['project-tag-all']
        self.assertEqual(all_pill.get('aria-current'), 'page')
        self.assertEqual(all_pill.get('href'), '/projects')

        for tag_name in ('agents', 'python', 'rag'):
            pill = parser.tag_pills[f'project-tag-{tag_name}']
            self.assertEqual(pill.get('href'), f'/projects?tag={tag_name}')
            self.assertIsNone(pill.get('aria-current'))

        # Three tags fit in the visible row; no disclosure is rendered.
        self.assertIsNone(parser.more_details_attrs)

    def test_selected_pill_is_active_and_narrows_results(self):
        response = self.client.get('/projects?tag=agents')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agents Starter')
        self.assertNotContains(response, 'RAG Search Engine')

        parser = _parse(response)
        active = parser.tag_pills['project-tag-agents']
        self.assertEqual(active.get('aria-current'), 'page')
        self.assertIn('bg-accent', active.get('class', ''))

        all_pill = parser.tag_pills['project-tag-all']
        self.assertIsNone(all_pill.get('aria-current'))
        self.assertEqual(all_pill.get('href'), '/projects')

    def test_card_tag_chips_are_links_to_filtered_listing(self):
        response = self.client.get('/projects')
        self.assertEqual(response.status_code, 200)

        parser = _parse(response)
        hrefs = {attrs.get('href') for attrs in parser.card_tag_links}
        self.assertIn('/projects?tag=agents', hrefs)
        self.assertIn('/projects?tag=rag', hrefs)
        # Chips must sit outside the card's own anchor — nested anchors
        # are invalid HTML and the browser would drop the inner link.
        self.assertEqual(parser.nested_card_tag_links, 0)

    def test_tag_pills_preserve_active_difficulty(self):
        response = self.client.get('/projects?difficulty=beginner')
        self.assertEqual(response.status_code, 200)

        parser = _parse(response)
        pill_query = _query(parser.tag_pills['project-tag-agents'])
        self.assertEqual(pill_query['tag'], ['agents'])
        self.assertEqual(pill_query['difficulty'], ['beginner'])

        all_query = _query(parser.tag_pills['project-tag-all'])
        self.assertEqual(all_query.get('difficulty'), ['beginner'])
        self.assertNotIn('tag', all_query)


@tag('core')
class ProjectsTagFilterOverflowTest(TestCase):
    """Beyond 12 tags, the remainder lives in a disclosure, not a cut."""

    @classmethod
    def setUpTestData(cls):
        Project.objects.create(
            title='Deep Tag Project',
            slug='deep-tag-project',
            description='Project with many topic tags',
            date=date(2026, 7, 3),
            tags=[f'topic-{i:02d}' for i in range(14)],
            published=True,
        )

    def test_overflow_tags_render_inside_closed_disclosure(self):
        response = self.client.get('/projects')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'More topics (+2)')

        parser = _parse(response)
        self.assertIsNotNone(parser.more_details_attrs)
        self.assertNotIn('open', parser.more_details_attrs)

        # Every tag remains reachable as a pill — nothing is truncated.
        for i in range(14):
            self.assertIn(f'project-tag-topic-{i:02d}', parser.tag_pills)

    def test_disclosure_opens_when_hidden_tag_is_selected(self):
        response = self.client.get('/projects?tag=topic-13')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Deep Tag Project')

        parser = _parse(response)
        self.assertIsNotNone(parser.more_details_attrs)
        self.assertIn('open', parser.more_details_attrs)
        active = parser.tag_pills['project-tag-topic-13']
        self.assertEqual(active.get('aria-current'), 'page')
