"""Collection/static page canonical and social metadata (issue #1378)."""

from datetime import date, timedelta
from html.parser import HTMLParser

from django.test import TestCase, override_settings
from django.utils import timezone

from content.models import (
    Article,
    Course,
    Download,
    MarketingPage,
    Project,
    Tutorial,
    Workshop,
)
from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

SITE_URL = 'https://aishippinglabs.com'
HOMEPAGE_TITLE = 'AI Shipping Labs | A Technical Community'


class _HeadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals = []
        self.meta = {}
        self.title = ''
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'link' and attributes.get('rel') == 'canonical':
            self.canonicals.append(attributes.get('href', ''))
        if tag == 'meta':
            key = attributes.get('property') or attributes.get('name')
            if key:
                self.meta.setdefault(key, []).append(
                    attributes.get('content', ''),
                )
        if tag == 'title':
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _head(response):
    parser = _HeadParser()
    parser.feed(response.content.decode())
    parser.title = parser.title.strip()
    return parser


@override_settings(SITE_BASE_URL=SITE_URL)
class CollectionPageMetadataTest(TestCase):
    ROUTES = {
        '/about': (
            'About | AI Shipping Labs',
            'Learn about AI Shipping Labs, its founders, and membership '
            'options for builders shipping AI projects.',
        ),
        '/blog': (
            'Blog | AI Shipping Labs',
            'Articles on AI engineering, MLOps, production systems, and '
            'building with data.',
        ),
        '/projects': (
            'Project Showcase | AI Shipping Labs',
            "Project ideas and real projects from people who've taken courses. "
            'End-to-end AI applications and agentic workflows you can learn '
            'from.',
        ),
        '/courses': (
            'Courses | AI Shipping Labs',
            'Structured courses on AI engineering, machine learning, and data '
            'engineering. Learn from hands-on projects and guided lessons.',
        ),
        '/events': (
            'Events | AI Shipping Labs',
            'Scheduled live community sessions, registration, calendar view, '
            'and recordings from past AI Shipping Labs events.',
        ),
        '/downloads': (
            'Downloads | AI Shipping Labs',
            'Downloadable resources for building AI agents and practical '
            'systems.',
        ),
        '/tutorials': (
            'Tutorials | AI Shipping Labs',
            'Easy to read tutorials on narrow topics. Learn how to do this and '
            'that.',
        ),
        '/workshops': (
            'Hands-on AI Workshops | AI Shipping Labs',
            'Hands-on AI workshops with recordings, step-by-step tutorials, '
            'code, and materials for builders shipping real projects.',
        ),
        '/workshops/catalog': (
            'All Workshops | AI Shipping Labs',
            'Browse the full AI Shipping Labs workshop catalog and archive, '
            'including recordings, writeups, tutorial pages, materials, and '
            'access labels.',
        ),
        '/resources': (
            'Curated Links | AI Shipping Labs',
            'Curated links to workshops, courses, articles, tools, and '
            'references for AI builders.',
        ),
        '/tags': (
            'Tags | AI Shipping Labs',
            'Browse all tags across articles, courses, recordings, projects, '
            'and resources.',
        ),
        '/pricing': (
            'Pricing | AI Shipping Labs',
            'Choose your membership tier. Free newsletter, or paid access to '
            'exclusive content, community, courses, and personalized feedback.',
        ),
    }

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def assert_page_metadata(self, response, path, title, description):
        self.assertEqual(response.status_code, 200)
        head = _head(response)
        expected_url = f'{SITE_URL}{path}'

        self.assertEqual(head.title, title)
        self.assertEqual(head.meta['description'], [description])
        self.assertEqual(head.canonicals, [expected_url])
        self.assertEqual(head.meta['og:url'], [expected_url])
        self.assertEqual(head.meta['og:title'], [title])
        self.assertEqual(head.meta['twitter:title'], [title])
        self.assertEqual(head.meta['og:description'], [description])
        self.assertEqual(head.meta['twitter:description'], [description])
        self.assertEqual(head.meta['og:type'], ['website'])
        self.assertEqual(
            head.meta['og:image'],
            [f'{SITE_URL}/static/ai-shipping-labs.jpg'],
        )
        self.assertEqual(head.meta['og:image:width'], ['1200'])
        self.assertEqual(head.meta['og:image:height'], ['630'])
        self.assertEqual(head.meta['og:image:alt'], ['AI Shipping Labs'])
        self.assertNotEqual(head.meta['og:title'], [HOMEPAGE_TITLE])
        self.assertNotEqual(head.meta['twitter:title'], [HOMEPAGE_TITLE])
        self.assertNotEqual(head.canonicals, [SITE_URL])
        self.assertNotEqual(head.meta['og:url'], [SITE_URL])

    def test_full_scoped_route_matrix_has_self_referencing_metadata(self):
        for path, (title, description) in self.ROUTES.items():
            with self.subTest(path=path):
                response = self.client.get(
                    f'{path}?utm_source=audit&unknown=value',
                )
                self.assert_page_metadata(
                    response, path, title, description,
                )

    @override_settings(ALLOWED_HOSTS=['testserver', 'attacker.example'])
    def test_configured_host_wins_over_untrusted_request_host(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://canonical.example',
            group='site',
        )
        clear_config_cache()

        response = self.client.get(
            '/pricing?checkout_error=temporarily_unavailable&utm_source=test',
            HTTP_HOST='attacker.example',
        )
        head = _head(response)

        self.assertEqual(head.canonicals, ['https://canonical.example/pricing'])
        self.assertEqual(
            head.meta['og:url'], ['https://canonical.example/pricing'],
        )
        self.assertContains(response, 'Checkout is temporarily unavailable')
        self.assertNotIn('attacker.example', response.content.decode())


@override_settings(SITE_BASE_URL=SITE_URL)
class CollectionFacetCanonicalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Agent Article',
            slug='agent-article-1378',
            description='Article description.',
            content_markdown='Article body.',
            date=date(2026, 8, 1),
            tags=['agents'],
            published=True,
        )
        cls.project = Project.objects.create(
            title='Agent Project',
            slug='agent-project-1378',
            description='Project description.',
            date=date(2026, 8, 1),
            difficulty='beginner',
            tags=['agents'],
            published=True,
        )
        cls.course = Course.objects.create(
            title='Python Course',
            slug='python-course-1378',
            description='Course description.',
            tags=['python'],
            status='published',
        )
        cls.download = Download.objects.create(
            title='Agent Download',
            slug='agent-download-1378',
            description='Download description.',
            tags=['agents'],
            published=True,
        )
        cls.workshop = Workshop.objects.create(
            title='Python Workshop',
            slug='python-workshop-1378',
            description='Workshop description.',
            date=date(2026, 8, 1),
            tags=['python'],
            core_tools=['Claude Code'],
            skill_level='beginner',
            status='published',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def assert_query_free_metadata(self, response, path):
        head = _head(response)
        expected_url = f'{SITE_URL}{path}'
        self.assertEqual(response.status_code, 200)
        self.assertEqual(head.canonicals, [expected_url])
        self.assertEqual(head.meta['og:url'], [expected_url])

    def test_collection_facets_remain_active_but_metadata_drops_queries(self):
        cases = [
            ('/blog?tag=agents', '/blog', 'selected_tags', ['agents']),
            (
                '/projects?difficulty=beginner&tag=agents&page=8',
                '/projects',
                'current_difficulty',
                'beginner',
            ),
            ('/courses?tag=python', '/courses', 'selected_tags', ['python']),
            ('/downloads?tag=agents', '/downloads', 'selected_tags', ['agents']),
            ('/resources?tag=python', '/resources', 'selected_tags', ['python']),
        ]
        for url, path, context_key, expected_context in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assert_query_free_metadata(response, path)
                self.assertEqual(response.context[context_key], expected_context)

    def test_workshop_catalog_facets_keep_state_on_catalog_canonical(self):
        response = self.client.get(
            '/workshops/catalog?topic=production-apps&tag=python'
            '&utm_source=test',
        )

        self.assert_query_free_metadata(response, '/workshops/catalog')
        self.assertEqual(response.context['selected_topic'], 'production-apps')
        self.assertEqual(response.context['selected_tags'], ['python'])
        self.assertContains(response, 'Python Workshop')

    def test_pricing_recovery_state_survives_query_canonicalization(self):
        response = self.client.get(
            '/pricing?checkout_error=temporarily_unavailable&utm_source=test',
        )

        self.assert_query_free_metadata(response, '/pricing')
        self.assertContains(response, 'Checkout is temporarily unavailable')


@override_settings(SITE_BASE_URL=SITE_URL)
class EventsCollectionMetadataTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        for index in range(25):
            Event.objects.create(
                title=f'Python Recording {index:02d}',
                slug=f'python-recording-1378-{index:02d}',
                description='Recorded event description.',
                start_datetime=now - timedelta(days=index + 2),
                end_datetime=now - timedelta(days=index + 2, hours=-1),
                status='completed',
                published=True,
                recording_url=f'https://video.example/{index}',
                tags=['python'],
            )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def assert_event_url(self, query, expected_url):
        response = self.client.get(f'/events{query}')
        head = _head(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(head.canonicals, [expected_url])
        self.assertEqual(head.meta['og:url'], [expected_url])
        return response, head

    def test_past_filter_has_distinct_title_description_and_canonical(self):
        response, head = self.assert_event_url(
            '?filter=past', f'{SITE_URL}/events?filter=past',
        )

        self.assertEqual(head.title, 'Past Event Recordings | AI Shipping Labs')
        self.assertEqual(
            head.meta['og:title'],
            ['Past Event Recordings | AI Shipping Labs'],
        )
        self.assertEqual(
            head.meta['twitter:title'],
            ['Past Event Recordings | AI Shipping Labs'],
        )
        description = head.meta['description'][0]
        self.assertIn('Browse recorded AI Shipping Labs events', description)
        self.assertEqual(head.meta['og:description'], [description])
        self.assertEqual(head.meta['twitter:description'], [description])
        self.assertEqual(response.context['filter_mode'], 'past')

    def test_past_tag_and_page_are_dropped_while_filter_state_stays_active(self):
        response, _head_metadata = self.assert_event_url(
            '?filter=past&tag=python&page=2',
            f'{SITE_URL}/events?filter=past',
        )

        self.assertEqual(response.context['filter_mode'], 'past')
        self.assertEqual(response.context['selected_tags'], ['python'])
        self.assertEqual(response.context['page_obj'].number, 2)
        self.assertContains(response, 'Filtered by:')
        self.assertContains(response, 'python')

    def test_filter_aliases_and_invalid_values_canonicalize_to_events(self):
        for query in ('', '?filter=all', '?filter=upcoming', '?filter=unknown'):
            with self.subTest(query=query):
                self.assert_event_url(query, f'{SITE_URL}/events')

    def test_actual_rendered_pagination_page_is_normalized(self):
        cases = [
            ('?page=1', f'{SITE_URL}/events'),
            ('?page=not-a-number', f'{SITE_URL}/events'),
            # Issue #1382: the default Upcoming view is not paginated, so a
            # stray ?page= param canonicalizes back to /events.
            ('?page=2', f'{SITE_URL}/events'),
            ('?page=999', f'{SITE_URL}/events'),
            (
                '?filter=past&page=2',
                f'{SITE_URL}/events?filter=past&page=2',
            ),
            (
                '?filter=past&page=999',
                f'{SITE_URL}/events?filter=past&page=2',
            ),
        ]
        for query, expected_url in cases:
            with self.subTest(query=query):
                self.assert_event_url(query, expected_url)


@override_settings(SITE_BASE_URL=SITE_URL)
class ExistingSeoRegressionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Detail Article',
            slug='detail-article-1378',
            description='Article detail description.',
            content_markdown='Article body.',
            date=date(2026, 8, 1),
            published=True,
        )
        cls.course = Course.objects.create(
            title='Detail Course',
            slug='detail-course-1378',
            description='Course detail description.',
            status='published',
        )
        cls.event = Event.objects.create(
            title='Detail Event',
            slug='detail-event-1378',
            description='Event detail description.',
            start_datetime=timezone.now() + timedelta(days=5),
            status='upcoming',
            published=True,
        )
        cls.tutorial = Tutorial.objects.create(
            title='Detail Tutorial',
            slug='detail-tutorial-1378',
            description='Tutorial detail description.',
            content_markdown='Tutorial body.',
            date=date(2026, 8, 1),
            published=True,
        )
        cls.workshop = Workshop.objects.create(
            title='Detail Workshop',
            slug='detail-workshop-1378',
            description='Workshop detail description.',
            date=date(2026, 8, 1),
            status='published',
        )
        cls.marketing_page = MarketingPage.objects.create(
            title='Detail Marketing Page',
            public_path='/detail-marketing-page-1378',
            description='Marketing page description.',
            meta_description='Marketing page metadata.',
            content_markdown='Marketing page body.',
            status='published',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_homepage_keeps_root_canonical_and_social_title(self):
        head = _head(self.client.get('/'))
        self.assertEqual(head.canonicals, [SITE_URL])
        self.assertEqual(head.meta['og:url'], [SITE_URL])
        self.assertEqual(head.meta['og:title'], [HOMEPAGE_TITLE])
        self.assertEqual(head.meta['twitter:title'], [HOMEPAGE_TITLE])

    def test_detail_pages_keep_model_specific_canonical_and_social_metadata(self):
        objects = (
            self.article,
            self.course,
            self.event,
            self.tutorial,
            self.workshop,
            self.marketing_page,
        )
        for obj in objects:
            with self.subTest(model=obj.__class__.__name__):
                response = self.client.get(obj.get_absolute_url())
                head = _head(response)
                expected_url = f'{SITE_URL}{obj.get_absolute_url()}'
                self.assertEqual(response.status_code, 200)
                self.assertEqual(head.canonicals, [expected_url])
                self.assertEqual(head.meta['og:url'], [expected_url])
                self.assertNotEqual(head.meta['og:title'], [HOMEPAGE_TITLE])
