"""Issue #523 — every catalog/preview card is fully clickable.

Server-side template tests cover the structural contract that Playwright then
verifies in the browser:

- The wrapping ``<a>`` is present and points to the right detail URL.
- Tag chips are NOT nested inside the wrapping ``<a>`` (no invalid HTML).
- The shared focus-visible ring class string is rendered on the wrapper.
- The homepage previews wrap the recording / article / project cards.

These are intentionally HTML-structural assertions (lxml / bs4) rather than
substring matches, because nested-anchor bugs only show up when you walk the
DOM.
"""

from __future__ import annotations

import datetime
from html.parser import HTMLParser

from django.test import Client, TestCase

from content.models import Article, CuratedLink, Download, Project, Tutorial
from events.models import Event

# Class string emitted by ``templates/content/_clickable_card_classes.html``.
# Hard-coded here intentionally: if someone changes the partial, this test
# fails and forces them to acknowledge the focus-ring contract is part of the
# clickable-card behaviour.
EXPECTED_FOCUS_CLASSES = (
    'focus-visible:outline-none '
    'focus-visible:ring-2 '
    'focus-visible:ring-accent '
    'focus-visible:ring-offset-2 '
    'focus-visible:ring-offset-background'
)


def _focus_classes_present(class_attr: str | None) -> bool:
    """Return True when every focus-visible token from the partial is on the
    element's class list. Order-independent."""
    if not class_attr:
        return False
    classes = set(class_attr.split())
    expected = set(EXPECTED_FOCUS_CLASSES.split())
    return expected.issubset(classes)


class _AnchorScan(HTMLParser):
    """Walk an HTML response and record every ``<a>`` href + class, plus
    whether each anchor was opened while another anchor was already open
    (= nested-anchor bug)."""

    def __init__(self):
        super().__init__()
        self.anchors: list[dict] = []
        self._anchor_depth = 0
        self.nested_anchor_hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        attr_dict = dict(attrs)
        href = attr_dict.get('href', '')
        cls = attr_dict.get('class', '')
        if self._anchor_depth > 0:
            self.nested_anchor_hrefs.append(href)
        self.anchors.append({'href': href, 'class': cls})
        self._anchor_depth += 1

    def handle_endtag(self, tag):
        if tag == 'a' and self._anchor_depth > 0:
            self._anchor_depth -= 1


def _scan_anchors(html: str) -> _AnchorScan:
    parser = _AnchorScan()
    parser.feed(html)
    return parser


class BlogListClickableCardTest(TestCase):
    """Reader clicks the empty area of a blog card and lands on the article."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Deploying ML Models',
            slug='deploying-ml-models',
            description='How to deploy ML models in production.',
            content_markdown='# Hello',
            author='Alice',
            tags=['mlops', 'python'],
            published=True,
            date=datetime.date(2026, 1, 3),
        )

    def test_blog_card_wraps_body_in_single_anchor_to_detail(self):
        """The card's body link points to /blog/<slug>."""
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)
        scan = _scan_anchors(response.content.decode())
        body_links = [a for a in scan.anchors if a['href'] == '/blog/deploying-ml-models']
        self.assertEqual(
            len(body_links), 1,
            'Exactly one wrapper <a> per published article should point at '
            'its detail URL.',
        )

    def test_blog_card_wrapper_carries_focus_visible_classes(self):
        """Keyboard users get a visible focus ring on the card wrapper."""
        response = self.client.get('/blog')
        scan = _scan_anchors(response.content.decode())
        body_link = next(
            a for a in scan.anchors if a['href'] == '/blog/deploying-ml-models'
        )
        self.assertTrue(
            _focus_classes_present(body_link['class']),
            f'Blog card wrapper missing focus-visible classes; got: {body_link["class"]!r}',
        )

    def test_blog_card_has_no_nested_anchors(self):
        """Tag chips on the blog list must not live inside the wrapping <a>."""
        response = self.client.get('/blog')
        scan = _scan_anchors(response.content.decode())
        self.assertEqual(
            scan.nested_anchor_hrefs, [],
            'Blog list contains nested <a> elements (invalid HTML); '
            f'nested hrefs: {scan.nested_anchor_hrefs}',
        )

    def test_blog_card_tag_chips_remain_anchors_to_filter_url(self):
        """Tag chips are still <a href> elements, just not nested."""
        response = self.client.get('/blog')
        scan = _scan_anchors(response.content.decode())
        # Tag URL filter format: /blog?tag=mlops or similar — assert at least
        # one anchor whose href contains the tag slug as a query param.
        tag_anchors = [
            a for a in scan.anchors
            if 'tag=mlops' in a['href'] or a['href'].endswith('=mlops')
        ]
        self.assertGreaterEqual(
            len(tag_anchors), 1,
            'Tag chip for "mlops" should still render as a real <a> link, '
            'just rendered outside the wrapping card anchor.',
        )


class DownloadsListClickableCardTest(TestCase):
    """Visitor clicks the empty area of a download card and reaches the
    file/signup/pricing destination matching their access state."""

    def test_lead_magnet_card_anonymous_wraps_to_signup(self):
        """Lead magnet (level=0) for anonymous: wrapper goes to signup-with-next."""
        Download.objects.create(
            title='Free Cheatsheet',
            slug='free-cheatsheet',
            description='Free for everyone.',
            file_url='https://example.com/cheatsheet.pdf',
            file_type='pdf',
            required_level=0,
            published=True,
        )
        response = self.client.get('/downloads')
        self.assertEqual(response.status_code, 200)
        scan = _scan_anchors(response.content.decode())
        wrapper_links = [
            a for a in scan.anchors
            if a['href'] == '/accounts/signup?next=/api/downloads/free-cheatsheet/file'
            and _focus_classes_present(a['class'])
        ]
        self.assertEqual(
            len(wrapper_links), 1,
            'Anonymous lead-magnet card body should wrap to signup-with-next, '
            f'with focus ring; got anchors: {scan.anchors}',
        )

    def test_gated_card_anonymous_wraps_to_pricing(self):
        """Gated download (required_level>0) without access: body links to /pricing."""
        Download.objects.create(
            title='Premium Slides',
            slug='premium-slides',
            description='For Premium tier only.',
            file_url='https://example.com/slides.pdf',
            file_type='slides',
            required_level=30,  # Premium
            published=True,
        )
        response = self.client.get('/downloads')
        scan = _scan_anchors(response.content.decode())
        wrapper_links = [
            a for a in scan.anchors
            if a['href'] == '/pricing' and _focus_classes_present(a['class'])
        ]
        self.assertGreaterEqual(
            len(wrapper_links), 1,
            'Gated download card body should wrap to /pricing for anonymous '
            f'visitors. Got: {scan.anchors}',
        )

    def test_no_nested_anchors_on_downloads_list(self):
        """Download list cards must not have nested <a> elements (the
        wrapping body link + the inner CTA + the tag chips must be siblings,
        not parent-and-child)."""
        Download.objects.create(
            title='Tagged Cheatsheet',
            slug='tagged-cheatsheet',
            description='Tag chips test.',
            file_url='https://example.com/x.pdf',
            file_type='pdf',
            required_level=0,
            tags=['ml', 'agents'],
            published=True,
        )
        response = self.client.get('/downloads')
        scan = _scan_anchors(response.content.decode())
        self.assertEqual(
            scan.nested_anchor_hrefs, [],
            f'Downloads list contains nested anchors: {scan.nested_anchor_hrefs}',
        )

    def test_inner_cta_remains_separate_anchor(self):
        """Sign Up / Download / View Pricing CTAs render as their own anchors,
        outside the wrapping body <a>."""
        Download.objects.create(
            title='Lead Magnet',
            slug='lead-magnet',
            description='Free.',
            file_url='https://example.com/x.pdf',
            file_type='pdf',
            required_level=0,
            published=True,
        )
        response = self.client.get('/downloads')
        body = response.content.decode()
        # The inner CTA is the same destination as the wrapper for lead magnets,
        # but it carries the bg-accent button styling. Both must exist.
        self.assertIn('Sign Up to Download', body)
        scan = _scan_anchors(body)
        signup_links = [
            a for a in scan.anchors
            if a['href'] == '/accounts/signup?next=/api/downloads/lead-magnet/file'
        ]
        self.assertEqual(
            len(signup_links), 2,
            'Lead-magnet card should produce two anchors with the same href: '
            'one for the body wrapper, one for the inner Sign Up CTA. '
            f'Got: {len(signup_links)}',
        )


class HomepageRecordingsCardTest(TestCase):
    """Visitor clicks the empty area of a homepage recording preview and
    lands on the recording detail."""

    @classmethod
    def setUpTestData(cls):
        from django.utils import timezone
        cls.event = Event.objects.create(
            title='RAG Workshop',
            slug='rag-workshop',
            description='Build a RAG pipeline live.',
            start_datetime=timezone.now() - datetime.timedelta(days=7),
            end_datetime=timezone.now() - datetime.timedelta(days=7, hours=-2),
            recording_url='https://youtu.be/abc',
            published=True,
            status='completed',
        )

    def test_homepage_recording_card_wraps_to_event_detail(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        scan = _scan_anchors(response.content.decode())
        wrapper_links = [
            a for a in scan.anchors
            if a['href'] == '/events/rag-workshop'
            and _focus_classes_present(a['class'])
        ]
        self.assertGreaterEqual(
            len(wrapper_links), 1,
            'Homepage #resources card should wrap to /events/<slug> with the '
            'shared focus-visible ring.',
        )

    def test_homepage_recording_card_no_nested_anchor(self):
        """The "View resource" trailing affordance must not be a second <a>
        inside the wrapper (it should be a <span> now)."""
        response = self.client.get('/')
        scan = _scan_anchors(response.content.decode())
        self.assertEqual(
            scan.nested_anchor_hrefs, [],
            f'Homepage contains nested anchors: {scan.nested_anchor_hrefs}',
        )


class HomepageBlogCardTest(TestCase):
    """Visitor clicks the empty area of a homepage blog preview and lands
    on the article."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='AI in Production',
            slug='ai-in-production',
            description='Running AI at scale.',
            content_markdown='# Body',
            author='Bob',
            published=True,
            date=datetime.date(2026, 1, 2),
        )

    def test_homepage_blog_card_wraps_to_article_detail(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        scan = _scan_anchors(response.content.decode())
        wrapper_links = [
            a for a in scan.anchors
            if a['href'] == '/blog/ai-in-production'
            and _focus_classes_present(a['class'])
        ]
        self.assertGreaterEqual(
            len(wrapper_links), 1,
            'Homepage #blog card should wrap to /blog/<slug> with focus ring.',
        )


class HomepageProjectsCardTest(TestCase):
    """Visitor clicks the empty area of a homepage project preview and lands
    on the project."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Agent Marketplace',
            slug='agent-marketplace',
            description='A marketplace for AI agents.',
            published=True,
            date=datetime.date(2026, 1, 1),
        )

    def test_homepage_project_card_wraps_to_project_detail(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        scan = _scan_anchors(response.content.decode())
        wrapper_links = [
            a for a in scan.anchors
            if a['href'] == '/projects/agent-marketplace'
            and _focus_classes_present(a['class'])
        ]
        self.assertGreaterEqual(
            len(wrapper_links), 1,
            'Homepage #projects card should wrap to /projects/<slug> with focus ring.',
        )


class CatalogFocusRingRegressionTest(TestCase):
    """Every catalog list whose cards are <a>-wrapped should expose the
    shared focus-visible ring on at least one card. Verifies the include
    landed on the verification-only templates."""

    @classmethod
    def setUpTestData(cls):
        cls.client = Client()
        cls.tutorial = Tutorial.objects.create(
            title='Tutorial',
            slug='tutorial-x',
            description='X',
            content_markdown='# x',
            published=True,
            date=datetime.date(2026, 1, 1),
        )
        cls.project = Project.objects.create(
            title='Proj',
            slug='proj-x',
            description='x',
            published=True,
            date=datetime.date(2026, 1, 1),
        )
        CuratedLink.objects.create(
            item_id='link-x',
            title='Link X',
            url='https://example.com',
            description='x',
            category='tools',
            required_level=0,
            published=True,
        )

    def _at_least_one_card_has_focus_ring(self, response):
        scan = _scan_anchors(response.content.decode())
        return any(
            _focus_classes_present(a['class']) for a in scan.anchors
        )

    def test_tutorials_list_card_has_focus_ring(self):
        response = Client().get('/tutorials')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self._at_least_one_card_has_focus_ring(response),
            'No tutorial card carries the shared focus-visible ring.',
        )

    def test_projects_list_card_has_focus_ring(self):
        response = Client().get('/projects')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self._at_least_one_card_has_focus_ring(response),
            'No project card carries the shared focus-visible ring.',
        )

    def test_resources_list_card_has_focus_ring(self):
        response = Client().get('/resources')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self._at_least_one_card_has_focus_ring(response),
            'No curated-link card carries the shared focus-visible ring.',
        )
