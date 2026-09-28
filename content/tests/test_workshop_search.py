"""Client-side workshop search index on /workshops and /workshops/catalog.

The search box reuses the shared curriculum syllabus search component; the
server's job is to render a hidden index of every published workshop and its
tutorial pages with explicit titles, search text, and target URLs.
"""

from datetime import date
from html.parser import HTMLParser

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from content.access import LEVEL_OPEN
from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage

CATALOG_URL = '/workshops/catalog'
LANDING_URL = '/workshops'


class _SearchIndexParser(HTMLParser):
    """Collect search-index items and search forms from rendered HTML."""

    def __init__(self):
        super().__init__()
        self.items = []
        self.forms = []
        self.inputs = []
        self.labels = []
        self._in_label = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-cb-syllabus-search-item' in attrs:
            self.items.append(attrs)
        if 'data-cb-syllabus-search' in attrs:
            self.forms.append(attrs)
        if 'data-cb-syllabus-search-input' in attrs:
            self.inputs.append(attrs)
        if tag == 'label' and attrs.get('for', '').startswith('workshops-search'):
            self._in_label = True
            self.labels.append('')

    def handle_endtag(self, tag):
        if tag == 'label':
            self._in_label = False

    def handle_data(self, data):
        if self._in_label:
            self.labels[-1] += data


def _parse(response):
    parser = _SearchIndexParser()
    parser.feed(response.content.decode())
    return parser


def _item_by_url(items, url):
    matches = [item for item in items if item.get('data-search-url') == url]
    assert len(matches) == 1, f'expected one index item for {url}, got {matches}'
    return matches[0]


def _workshop(slug, title, *, day, tags=None, status='published', **extra):
    return Workshop.objects.create(
        slug=slug, title=title, status=status, date=date(2026, 8, day),
        tags=tags or [], landing_required_level=LEVEL_OPEN,
        pages_required_level=LEVEL_OPEN, recording_required_level=LEVEL_OPEN,
        **extra,
    )


class WorkshopSearchIndexTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rag = _workshop(
            'rag-from-scratch', 'Build RAG from scratch', day=20,
            tags=['rag', 'elasticsearch'], skill_level='intermediate',
            core_tools=['Elasticsearch'],
            description='Retrieval pipelines with hybrid search.',
        )
        instructor = Instructor.objects.create(
            instructor_id='maria-search', name='Maria Searcher',
        )
        WorkshopInstructor.objects.create(
            workshop=cls.rag, instructor=instructor, position=0,
        )
        cls.chunking = WorkshopPage.objects.create(
            workshop=cls.rag, slug='chunking', title='Chunking documents',
            sort_order=1, body='Secret gated body text about embeddings.',
        )
        cls.agents = _workshop(
            'agents-101', 'Agents 101', day=19, tags=['ai-agents'],
        )
        # Older than the landing preview limit (4) so it only reaches the
        # landing page through the search index.
        for day in range(10, 15):
            _workshop(f'filler-{day}', f'Filler workshop {day}', day=day)
        cls.oldest = _workshop(
            'oldest-evals', 'Evaluation basics', day=1, tags=['evals'],
        )
        cls.draft = _workshop(
            'draft-rag', 'Draft RAG workshop', day=21, status='draft',
        )

    def test_catalog_indexes_every_published_workshop_with_its_url(self):
        items = _parse(self.client.get(CATALOG_URL)).items

        workshop_urls = {
            item['data-search-url'] for item in items
            if '/' not in item['data-search-url'].removeprefix('/workshops/')
        }
        expected = set(
            Workshop.objects.filter(status='published').values_list('slug', flat=True)
        )
        self.assertEqual(workshop_urls, {f'/workshops/{slug}' for slug in expected})
        self.assertNotIn(
            '/workshops/draft-rag',
            [item['data-search-url'] for item in items],
        )

    def test_workshop_item_search_text_includes_card_metadata(self):
        items = _parse(self.client.get(CATALOG_URL)).items

        rag = _item_by_url(items, '/workshops/rag-from-scratch')
        self.assertEqual(rag['data-search-title'], 'Build RAG from scratch')
        for fragment in (
            'Retrieval pipelines with hybrid search.',
            'elasticsearch',
            'Elasticsearch',
            'Maria Searcher',
            self.rag.skill_level_label,
        ):
            self.assertIn(fragment, rag['data-search-text'])
        self.assertEqual(
            rag['data-search-excerpt'], 'Retrieval pipelines with hybrid search.',
        )

    def test_tutorial_pages_are_indexed_with_page_url_and_workshop_context(self):
        items = _parse(self.client.get(CATALOG_URL)).items

        page = _item_by_url(items, self.chunking.get_absolute_url())
        self.assertEqual(page['data-search-url'], '/workshops/rag-from-scratch/chunking')
        self.assertEqual(page['data-search-title'], 'Chunking documents')
        self.assertIn('Build RAG from scratch', page['data-search-text'])
        # Only titles are indexed; gated page bodies never reach the HTML.
        self.assertNotIn('Secret gated body text', page['data-search-text'])
        self.assertNotContains(
            self.client.get(CATALOG_URL), 'Secret gated body text',
        )

    def test_topic_filter_scopes_the_search_index_to_listed_workshops(self):
        response = self.client.get(CATALOG_URL, {'topic': 'ai-agents'})

        self.assertEqual(
            [w.slug for w in response.context['workshops']], ['agents-101'],
        )
        urls = [item['data-search-url'] for item in _parse(response).items]
        self.assertEqual(urls, ['/workshops/agents-101'])

    def test_landing_indexes_workshops_beyond_the_preview_limit(self):
        response = self.client.get(LANDING_URL)

        preview_slugs = [w.slug for w in response.context['workshops']]
        self.assertNotIn('oldest-evals', preview_slugs)
        urls = [item['data-search-url'] for item in _parse(response).items]
        self.assertIn('/workshops/oldest-evals', urls)
        self.assertIn('/workshops/rag-from-scratch/chunking', urls)

    def test_search_form_uses_workshop_copy_and_inline_results(self):
        parser = _parse(self.client.get(CATALOG_URL))

        self.assertEqual(len(parser.forms), 1)
        form = parser.forms[0]
        # No submit URL: results render inline on the catalog page.
        self.assertNotIn('action', form)
        self.assertEqual(
            form['data-cb-syllabus-search-no-matches-status'],
            'No matching workshops',
        )
        self.assertEqual(
            parser.inputs[0]['placeholder'],
            'Search workshops or tutorial pages...',
        )
        self.assertEqual(parser.labels, ['Search workshops'])

    def test_index_query_count_does_not_grow_with_pages(self):
        self.client.get(CATALOG_URL)  # warm per-process caches
        baseline = self._catalog_queries()
        for index in range(5):
            WorkshopPage.objects.create(
                workshop=self.agents, slug=f'extra-{index}',
                title=f'Extra page {index}', sort_order=index,
            )
        self.assertEqual(self._catalog_queries(), baseline)

    def _catalog_queries(self):
        with CaptureQueriesContext(connection) as queries:
            self.client.get(CATALOG_URL)
        return len(queries)


class WorkshopSearchEmptyArchiveTest(TestCase):
    def test_no_search_box_without_published_workshops(self):
        response = self.client.get(CATALOG_URL)

        parser = _parse(response)
        self.assertEqual(parser.forms, [])
        self.assertEqual(parser.items, [])
