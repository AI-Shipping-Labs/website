"""Course Home syllabus search: the Home page renders the hidden index.

The client-side search on ``/courses/<slug>/home`` indexes the syllabus rows
in its ``data-cb-syllabus`` scope, so Home must render the full syllabus
(hidden) even though the visible Home layout does not show it.
"""

from html.parser import HTMLParser

from django.test import TestCase

from accounts.models import User
from content.models import Course, Module, Unit


class _SearchScopeParser(HTMLParser):
    """Collect unit hrefs of search items inside the Home search scope."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.scope_depth = None
        self.search_item_hrefs = []
        self.search_item_texts = []
        self.search_forms = []
        self.item_depth = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'br', 'img', 'input', 'meta', 'link', 'hr', 'source'}:
            self._check(attrs)
            return
        self.depth += 1
        if self.scope_depth is None and 'data-cb-syllabus' in attrs:
            self.scope_depth = self.depth
        self._check(attrs)
        if (
            self.scope_depth not in (None, -1)
            and self.item_depth is None
            and 'data-cb-syllabus-search-item' in attrs
        ):
            self.item_depth = self.depth
            self.search_item_texts.append('')

    def handle_data(self, data):
        if self.item_depth is not None:
            self.search_item_texts[-1] += data

    def _check(self, attrs):
        if self.scope_depth in (None, -1):
            return
        if 'data-cb-syllabus-search-item' in attrs:
            self.search_item_hrefs.append(attrs.get('href', ''))
        if 'data-cb-syllabus-search' in attrs:
            self.search_forms.append(attrs)

    def handle_endtag(self, tag):
        if self.item_depth is not None and self.depth == self.item_depth:
            self.item_depth = None
        if self.scope_depth is not None and self.depth == self.scope_depth:
            self.scope_depth = -1  # scope closed; stop collecting
        self.depth -= 1


class CourseHomeSyllabusSearchTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='course-home-search@example.com', password='testpass',
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Searchable course', slug='course-home-search',
            status='published', required_level=0,
        )
        module = Module.objects.create(
            course=cls.course, title='Retrieval basics', slug='retrieval-basics',
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=module, title='Vector embeddings explained',
            slug='vector-embeddings', sort_order=1,
        )

    def test_home_renders_syllabus_units_as_search_index(self):
        self.client.force_login(self.user)

        response = self.client.get('/courses/course-home-search/home')

        parser = _SearchScopeParser()
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.search_forms), 1)
        self.assertIn(self.unit.get_absolute_url(), parser.search_item_hrefs)
        item_texts = [' '.join(text.split()) for text in parser.search_item_texts]
        self.assertTrue(
            any('Vector embeddings explained' in text for text in item_texts),
            item_texts,
        )
