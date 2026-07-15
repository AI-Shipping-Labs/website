"""Tests for the ``strip_markdown`` teaser filter and its template surfaces.

Issue #917: list/card teasers rendered raw markdown ``description`` strings, so
a markdown link ``[label](url)`` leaked its literal ``[``/``]``/``(`` into the
teaser. The shared ``strip_markdown`` filter (content/templatetags/teaser_tags)
renders markdown to HTML, strips tags, unescapes entities, and collapses
whitespace so teasers read as clean plain text. Detail-page bodies stay fully
rendered.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from content.models import (
    Article,
    Course,
    CuratedLink,
    Download,
    Project,
    Tutorial,
)
from content.templatetags.teaser_tags import strip_markdown
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()

# A markdown link whose bracket leaks into a raw-markdown teaser. Reused across
# surfaces so each test asserts the same label-shows / syntax-gone contract.
LINK_MD = 'We take the FAQ agent from the [End-to-End Agent Deployment](https://x) tutorial.'
LINK_LABEL = 'End-to-End Agent Deployment'
RAW_LINK_FRAGMENT = '[End-to-End Agent Deployment]'


@tag('core')
class StripMarkdownFilterTest(TestCase):
    """Unit table for the filter itself (Rule 18 parameterized rows)."""

    def test_markdown_inputs_reduce_to_plain_text(self):
        cases = [
            ('[label](https://example.com)', 'label'),
            ('**bold**', 'bold'),
            ('_italic_', 'italic'),
            ('`code`', 'code'),
            ('**bold** and _italic_ and `code`', 'bold and italic and code'),
            ('# Heading\n\nBody text.', 'Heading Body text.'),
            ('Para one.\n\nPara two.\n\nPara three.', 'Para one. Para two. Para three.'),
            ('', ''),
            (None, ''),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(strip_markdown(raw), expected)

    def test_link_drops_brackets_and_url(self):
        result = strip_markdown('[End-to-End Agent Deployment](https://x)')
        self.assertEqual(result, 'End-to-End Agent Deployment')
        self.assertNotIn('[', result)
        self.assertNotIn(']', result)
        self.assertNotIn('(', result)
        self.assertNotIn('https://x', result)

    def test_emphasis_drops_markers(self):
        result = strip_markdown('**bold** and _italic_ and `code`')
        self.assertNotIn('*', result)
        self.assertNotIn('_', result)
        self.assertNotIn('`', result)

    def test_html_entities_unescaped(self):
        # render_markdown escapes & < > as entities; the excerpt must be true
        # plain text, not entity-encoded.
        self.assertEqual(strip_markdown('A & B < C'), 'A & B < C')

    def test_event_widget_directive_is_semantically_dropped(self):
        result = strip_markdown(
            'Intro.\n\n```eventwidget\nslug: inactive-widget\n```\n\nOutro.'
        )
        self.assertEqual(result, 'Intro. Outro.')
        self.assertNotIn('eventwidget', result)
        self.assertNotIn('slug:', result)
        self.assertNotIn('Loading', result)

    def test_truncated_event_widget_directive_is_dropped(self):
        # Legacy auto-descriptions may have sliced source markdown before the
        # closing fence. The semantic directive still must not reach a header
        # or excerpt.
        result = strip_markdown(
            'Intro.\n\n```eventwidget\nslug: inactive-widget'
        )
        self.assertEqual(result, 'Intro.')
        self.assertNotIn('eventwidget', result)
        self.assertNotIn('slug:', result)

    def test_truncation_chains_after_strip(self):
        long_md = '[Click here](https://x) ' + 'word ' * 50
        from django.template.defaultfilters import truncatechars
        result = truncatechars(strip_markdown(long_md), 20)
        self.assertTrue(result.endswith('…'))
        self.assertNotIn('[', result)
        self.assertLessEqual(len(result), 21)


@tag('core')
class DashboardRecentContentTeaserTest(TierSetupMixin, TestCase):
    """Homepage Recent Content card teaser shows plain text (the reported bug)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='reader917@example.com', password='testpass',
        )
        self.client.login(email='reader917@example.com', password='testpass')

    def test_article_teaser_has_no_markdown_syntax(self):
        Article.objects.create(
            title='Deploying Vector Search', slug='deploying-vector-search',
            description=LINK_MD, date=date.today(), published=True,
        )
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # Scope to the recent-content section so the assertion is element-bound.
        self.assertContains(response, 'data-testid="dashboard-recent-content"')
        self.assertContains(
            response,
            'We take the FAQ agent from the End-to-End Agent Deployment tutorial',
        )
        self.assertNotContains(response, RAW_LINK_FRAGMENT)


@tag('core')
class ListingTeaserTest(TierSetupMixin, TestCase):
    """Each listing surface renders the link label but not the raw markdown."""

    def _assert_clean_teaser(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        self.assertContains(response, LINK_LABEL)
        self.assertNotContains(response, RAW_LINK_FRAGMENT)

    def test_blog_listing(self):
        Article.objects.create(
            title='Blog Item', slug='blog-item-917',
            description=LINK_MD, date=date.today(), published=True,
        )
        self._assert_clean_teaser('/blog')

    def test_tutorials_listing(self):
        Tutorial.objects.create(
            title='Tutorial Item', slug='tutorial-item-917',
            description=LINK_MD, date=date.today(), published=True,
        )
        self._assert_clean_teaser('/tutorials')

    def test_projects_listing(self):
        Project.objects.create(
            title='Project Item', slug='project-item-917',
            description=LINK_MD, date=date.today(), published=True,
        )
        self._assert_clean_teaser('/projects')

    def test_resources_listing(self):
        CuratedLink.objects.create(
            item_id='link-917', title='Link Item',
            url='https://example.com', category='workshops',
            description=LINK_MD, published=True,
        )
        self._assert_clean_teaser('/resources')

    def test_downloads_listing(self):
        Download.objects.create(
            title='Download Item', slug='download-item-917',
            file_url='https://example.com/file.pdf', file_type='pdf',
            description=LINK_MD, required_level=0, published=True,
        )
        self._assert_clean_teaser('/downloads')

    def test_courses_listing(self):
        # Course renders description_html on save; the listing strips its tags.
        Course.objects.create(
            title='Course Item', slug='course-item-917',
            description=LINK_MD, status='published',
        )
        self._assert_clean_teaser('/courses')

    def test_tag_detail_listing(self):
        Article.objects.create(
            title='Tagged Article', slug='tagged-article-917',
            description=LINK_MD, date=date.today(), published=True,
            tags=['python917'],
        )
        self._assert_clean_teaser('/tags/python917')


@tag('core')
class DetailBodyUnchangedTest(TestCase):
    """Regression guard: detail-page bodies stay fully rendered markdown."""

    def test_article_detail_body_keeps_clickable_link(self):
        Article.objects.create(
            title='Detail Article', slug='detail-article-917',
            description='A short teaser.',
            content_markdown='See [the guide](https://example.com/guide) for details.',
            date=date.today(), published=True,
        )
        response = self.client.get('/blog/detail-article-917')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="https://example.com/guide"')
        self.assertContains(response, '<a')


@tag('core')
class ReferenceSurfacesStayCleanTest(TierSetupMixin, TestCase):
    """Workshops/events teasers were already correct — guard against regression."""

    def test_events_listing_recording_teaser_clean(self):
        Event.objects.create(
            title='Recorded Event', slug='recorded-event-917',
            description=LINK_MD,
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=917',
            published=True,
        )
        response = self.client.get('/events')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, RAW_LINK_FRAGMENT)
