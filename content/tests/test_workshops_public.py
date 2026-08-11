"""Tests for the public Workshop surface (issue #296).

Covers:
- ``/workshops`` landing page with a latest-workshops preview and catalog CTA.
- ``/workshops/catalog`` catalog (published only, draft hidden, tier badges,
  empty state, tag filter).
- ``/workshops/<slug>`` landing page (404 on draft / unknown, SEO content
  always rendered, landing-level paywall, pages list with locks).
- ``/workshops/<slug>/video`` recording page (gates by recording level,
  anonymous gets a paywall not a 403, recording embeds when accessible).
- ``/workshops/<slug>/tutorial/<page_slug>`` page detail (404 on bad
  page slug, prev/next ordering, gated visitors get the paywall not a
  403, body rendered when accessible).
- Sitemap includes workshops + pages, draft workshops excluded.
- Cross-links: events_list past cards switch to /workshops/<slug> when a
  workshop is linked, event_detail surfaces the writeup card.
- Workshop JSON-LD (Course schema) emitted on the landing page.
"""

from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    LEVEL_REGISTERED,
)
from content.models import (
    Instructor,
    Workshop,
    WorkshopInstructor,
    WorkshopPage,
)
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()

WORKSHOPS_LANDING_URL = '/workshops'
WORKSHOPS_CATALOG_URL = '/workshops/catalog'


def _attach_workshop_instructor(workshop, name, position=0):
    instructor, _ = Instructor.objects.get_or_create(
        name=name,
        defaults={
            'instructor_id': name.lower().replace(' ', '-'),
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.create(
        workshop=workshop, instructor=instructor, position=position,
    )
    return instructor


def _make_event(**kwargs):
    """Create a published past Event, optionally configured as a workshop.

    Issue #713: "past" is now time-derived, so the default fixture sets
    ``start_datetime`` / ``end_datetime`` in the past (rather than
    relying on ``status='completed'`` alone).
    """
    from datetime import timedelta

    now = timezone.now()
    defaults = {
        'slug': 'default-event',
        'title': 'Event',
        'start_datetime': now - timedelta(hours=3),
        'end_datetime': now - timedelta(hours=1),
        'status': 'completed',
        'kind': 'standard',
        'recording_url': '',
        'published': True,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _make_workshop(slug='ws', title='Workshop', status='published',
                   landing=0, pages=10, recording=20, with_event=False,
                   recording_url='https://www.youtube.com/watch?v=abc',
                   materials=None, code_repo_url='', cover_image_url='',
                   custom_banner_url='', auto_banner_url='',
                   description='# Hello\n\nDescription text.',
                   tags=None, instructor='Alice', skill_level='',
                   core_tools=None):
    """Create a workshop (and optional linked event) for tests."""
    event = None
    if with_event:
        event = _make_event(
            slug=slug + '-event',
            title=title,
            kind='workshop',
            recording_url=recording_url,
            materials=materials or [],
        )
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        status=status,
        date=date(2026, 4, 21),
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
        code_repo_url=code_repo_url,
        cover_image_url=cover_image_url,
        custom_banner_url=custom_banner_url,
        auto_banner_url=auto_banner_url,
        tags=tags or [],
        skill_level=skill_level,
        core_tools=core_tools or [],
        event=event,
    )
    if instructor:
        _attach_workshop_instructor(workshop, instructor)
    return workshop


def _make_page(workshop, slug, title, sort_order, body='Hello'):
    return WorkshopPage.objects.create(
        workshop=workshop,
        slug=slug,
        title=title,
        sort_order=sort_order,
        body=body,
    )


class WorkshopContentDocsTest(SimpleTestCase):
    def test_workshop_docs_document_core_tools_frontmatter(self):
        docs = Path('_docs/content.md').read_text(encoding='utf-8')

        self.assertIn('core_tools:', docs)
        self.assertIn('- Claude Code', docs)
        self.assertIn('public workshop catalog', docs)
        self.assertIn('/workshops/catalog?tool=...', docs)


def _opening_anchor_for_testid(body, testid):
    testid_pos = body.index(f'data-testid="{testid}"')
    tag_start = body.rfind('<a', 0, testid_pos)
    tag_end = body.index('>', testid_pos)
    return body[tag_start:tag_end]


def _opening_tag_for_testid(body, tag_name, testid):
    testid_pos = body.index(f'data-testid="{testid}"')
    tag_start = body.rfind(f'<{tag_name}', 0, testid_pos)
    tag_end = body.index('>', testid_pos)
    return body[tag_start:tag_end]


def _workshop_card_html(response, slug):
    body = response.content.decode()
    marker = f'data-workshop-slug="{slug}"'
    if marker not in body:
        raise AssertionError(f'Workshop card not found for slug {slug!r}')
    return body.split(marker, 1)[1].split('</article>', 1)[0]


class _NestedAnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchor_depth = 0
        self.found_nested_anchor = False

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        if self.anchor_depth > 0:
            self.found_nested_anchor = True
        self.anchor_depth += 1

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor_depth > 0:
            self.anchor_depth -= 1


class WorkshopsCatalogTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.published = _make_workshop(
            slug='one', title='Visible Workshop', tags=['python', 'agents'],
        )
        cls.draft = _make_workshop(
            slug='two', title='Hidden Draft', status='draft',
        )

    def test_catalog_lists_published_only(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/workshops_catalog.html')
        self.assertTemplateUsed(response, 'content/_workshops_catalog.html')
        self.assertContains(response, 'All workshops')
        self.assertContains(response, 'Visible Workshop')
        self.assertNotContains(response, 'Hidden Draft')

    def test_catalog_renders_curated_topics_from_published_tags_only(self):
        _make_workshop(
            slug='rag-topic',
            title='RAG Topic',
            tags=['rag', 'function-calling', 'evaluation'],
        )
        _make_workshop(
            slug='career-topic',
            title='Career Topic',
            tags=['personal-brand'],
        )
        _make_workshop(
            slug='secret-topic',
            title='Secret Topic Draft',
            status='draft',
            tags=['coding-assistants'],
        )

        response = self.client.get(WORKSHOPS_CATALOG_URL)

        self.assertEqual(
            [topic['slug'] for topic in response.context['topic_filters']],
            ['ai-agents', 'rag-search', 'production-apps', 'career'],
        )
        self.assertContains(response, 'data-testid="workshop-topic-filter"')
        self.assertContains(response, 'data-testid="workshop-topic-all"')
        self.assertContains(response, 'data-testid="workshop-topic-ai-agents"')
        self.assertContains(response, 'data-testid="workshop-topic-rag-search"')
        self.assertContains(response, 'data-testid="workshop-topic-career"')
        self.assertNotContains(
            response, 'data-testid="workshop-topic-coding-with-ai"',
        )
        self.assertNotContains(response, 'workshop-facet-topic')
        self.assertNotContains(response, 'workshop-facet-technology')
        self.assertNotContains(response, 'secret-topic')
        self.assertNotContains(response, 'Secret Topic Draft')

    def test_landing_renders_visitor_offer_preview_and_catalog_cta(self):
        # A second published workshop (newer) so the landing features the
        # latest one and still renders the remaining preview in the grid.
        newest = _make_workshop(
            slug='newest', title='Newest Workshop', tags=['python'],
        )
        newest.date = date(2026, 9, 1)
        newest.save()

        response = self.client.get(WORKSHOPS_LANDING_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/workshops_list.html')
        self.assertTemplateUsed(response, 'content/_workshops_catalog.html')
        self.assertContains(response, 'data-testid="workshops-landing"')
        self.assertContains(response, 'Hands-on AI workshops')
        self.assertContains(response, 'Practical AI engineering sessions')
        self.assertContains(response, 'a step-by-step tutorial')
        self.assertContains(response, 'runnable code or materials')
        # Value props moved to the "How workshops work" trio at the bottom.
        self.assertContains(response, 'How workshops work')
        self.assertContains(response, 'Guided build flow')
        self.assertContains(response, 'Replay and tutorials')
        self.assertContains(response, 'Project outcomes')
        # The plural heading owns the featured row and three-card preview.
        self.assertContains(response, 'Latest workshops')
        self.assertNotContains(response, 'More workshops')
        self.assertContains(response, 'scroll-mt-24 pt-6 pb-6')
        self.assertContains(response, 'data-testid="featured-workshop-card"')
        self.assertContains(response, 'Newest Workshop')
        # Both hero CTAs are gone: the catalog is on the same page now and
        # Membership is a top-level nav link.
        self.assertNotContains(response, 'data-testid="browse-workshops-cta"')
        self.assertNotContains(response, 'Browse all workshops')
        self.assertNotContains(response, 'data-testid="view-membership-options-cta"')
        self.assertNotContains(response, 'View membership options')
        self.assertContains(response, 'href="/workshops/catalog"')
        self.assertContains(response, 'id="workshop-preview"')
        self.assertContains(response, 'data-testid="workshops-preview"')
        # The embedded catalog header is suppressed on the landing.
        self.assertNotContains(response, 'Start with recent workshop writeups')
        self.assertContains(response, 'data-testid="view-all-workshops-preview-cta"')
        self.assertNotContains(response, 'data-testid="workshop-access-filters"')

        body = response.content.decode()
        landing_index = body.index('data-testid="workshops-landing"')
        featured_index = body.index('data-testid="featured-workshop-card"')
        preview_index = body.index('data-testid="workshops-preview"')
        card_index = body.index('data-testid="workshop-card"')
        value_points_index = body.index('aria-label="Workshop value points"')
        self.assertLess(landing_index, featured_index)
        self.assertLess(featured_index, preview_index)
        self.assertLess(preview_index, card_index)
        # The catalog/preview now precedes the value-prop trio at the bottom.
        self.assertLess(card_index, value_points_index)

    def test_landing_featured_row_is_followed_by_three_cards_without_second_heading(self):
        for index in range(3):
            workshop = _make_workshop(
                slug=f'landing-preview-{index}',
                title=f'Landing Preview {index}',
            )
            workshop.date = date(2026, 9, index + 1)
            workshop.save(update_fields=['date'])

        response = self.client.get(WORKSHOPS_LANDING_URL)

        self.assertContains(response, 'Latest workshops')
        self.assertNotContains(response, 'More workshops')
        self.assertContains(
            response, 'data-testid="featured-workshop-card"', count=1,
        )
        self.assertContains(response, 'data-testid="workshop-card"', count=3)

        body = response.content.decode()
        self.assertLess(
            body.index('data-testid="featured-workshop-card"'),
            body.index('data-testid="workshops-preview"'),
        )

    @tag('visual_regression')
    def test_landing_uses_single_column_heading_and_value_point_layout(self):
        response = self.client.get(WORKSHOPS_LANDING_URL)

        self.assertContains(
            response,
            'class="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl"',
        )
        self.assertContains(
            response,
            'class="mt-5 grid gap-6 sm:grid-cols-3" '
            'aria-label="Workshop value points"',
        )
        self.assertNotContains(response, 'lg:grid-cols-[')
        self.assertNotContains(response, 'lg:grid-cols-1')
        self.assertNotContains(response, 'lg:text-5xl')

    def test_catalog_metadata_describes_workshop_landing_offer(self):
        response = self.client.get(WORKSHOPS_LANDING_URL)

        self.assertContains(
            response,
            '<title>Hands-on AI Workshops | AI Shipping Labs</title>',
            html=True,
        )
        self.assertContains(response, 'Hands-on AI workshops with recordings')
        self.assertContains(response, 'step-by-step tutorials')
        self.assertContains(response, 'code, and materials')

    def test_catalog_route_has_archive_metadata_without_landing_copy(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)

        self.assertContains(
            response,
            '<title>All Workshops | AI Shipping Labs</title>',
            html=True,
        )
        self.assertContains(
            response,
            'Browse the full AI Shipping Labs workshop catalog and archive',
        )
        self.assertContains(response, 'data-testid="workshop-catalog"')
        self.assertContains(response, 'All workshops')
        self.assertNotContains(response, 'data-testid="workshops-landing"')
        self.assertNotContains(response, 'Practical AI engineering sessions')

    def test_catalog_shows_tier_badge_when_pages_gated(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        self.assertContains(response, 'data-testid="workshop-tier-badge"')
        # Issue #481: badges read "Basic or above" not "Basic+".
        self.assertContains(response, 'Basic or above')
        self.assertNotContains(response, 'Basic+')

    def test_catalog_card_hierarchy_labels_access_and_metadata(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        card = _workshop_card_html(response, 'one')

        self.assertIn('data-testid="workshop-card-primary-signals"', card)
        # No "Workshop" type badge: the partial only renders on
        # workshop-only surfaces, so the badge restated the page.
        self.assertNotIn('data-testid="workshop-card-type"', card)
        self.assertNotIn('data-testid="workshop-card-access"', card)
        self.assertNotIn('>Access<', card)
        self.assertIn('data-testid="workshop-tier-badge"', card)
        self.assertIn('data-component="member-badge"', card)
        self.assertIn('data-required-level="10"', card)
        self.assertIn('data-lucide="lock"', card)
        self.assertIn('Basic or above', card)
        self.assertIn('data-testid="workshop-card-title"', card)
        self.assertIn('Visible Workshop', card)
        self.assertIn('data-testid="workshop-card-metadata"', card)
        # The meta row is icon + value; the literal field-name words
        # "Instructor" and "Date" were removed. Assert the values and
        # their identifying testids instead.
        self.assertIn('data-testid="workshop-card-instructor"', card)
        self.assertIn('Alice', card)
        self.assertIn('data-testid="workshop-card-date"', card)
        self.assertIn('Apr 21, 2026', card)
        self.assertIn('data-testid="workshop-card-description"', card)
        self.assertIn('Description text.', card)
        # The unified card matches the book card density: no per-card topic
        # chips, tools, or deliverable ("Includes") rows — those live on the
        # workshop detail page.
        self.assertNotIn('data-testid="workshop-card-topics"', card)
        self.assertNotIn('data-testid="workshop-card-tools"', card)
        self.assertNotIn('data-testid="workshop-card-deliverables"', card)

        # Order: badge cluster (header) -> title -> description -> bottom meta.
        self.assertLess(
            card.index('data-testid="workshop-card-primary-signals"'),
            card.index('data-testid="workshop-card-title"'),
        )
        self.assertLess(
            card.index('data-testid="workshop-card-title"'),
            card.index('data-testid="workshop-card-description"'),
        )
        self.assertLess(
            card.index('data-testid="workshop-card-description"'),
            card.index('data-testid="workshop-card-metadata"'),
        )

    def test_catalog_card_access_label_uses_pages_required_level(self):
        Workshop.objects.all().delete()
        _make_workshop(
            slug='open-card', title='Open Card',
            landing=LEVEL_OPEN, pages=LEVEL_OPEN, recording=LEVEL_OPEN,
            description='', instructor=None,
        )
        _make_workshop(
            slug='registered-card', title='Registered Card',
            landing=LEVEL_OPEN, pages=LEVEL_REGISTERED,
            recording=LEVEL_REGISTERED, description='', instructor=None,
        )
        _make_workshop(
            slug='basic-card', title='Basic Card',
            landing=LEVEL_OPEN, pages=LEVEL_BASIC, recording=LEVEL_MAIN,
            description='', instructor=None,
        )
        _make_workshop(
            slug='main-card', title='Main Card',
            landing=LEVEL_OPEN, pages=LEVEL_MAIN, recording=LEVEL_MAIN,
            description='', instructor=None,
        )
        _make_workshop(
            slug='premium-card', title='Premium Card',
            landing=LEVEL_OPEN, pages=LEVEL_PREMIUM,
            recording=LEVEL_PREMIUM, description='', instructor=None,
        )

        response = self.client.get(WORKSHOPS_CATALOG_URL)

        open_card = _workshop_card_html(response, 'open-card')
        self.assertIn('data-testid="workshop-free-badge"', open_card)
        self.assertIn('data-required-level="0"', open_card)
        self.assertIn('data-lucide="badge-check"', open_card)
        self.assertIn('Free', open_card)
        self.assertIn('bg-green-500/15', open_card)
        registered_card = _workshop_card_html(response, 'registered-card')
        self.assertIn('data-testid="workshop-free-badge"', registered_card)
        self.assertIn('data-required-level="5"', registered_card)
        self.assertIn('data-lucide="badge-check"', registered_card)
        self.assertIn('Free with sign-in', registered_card)
        self.assertIn('bg-green-500/15', registered_card)
        basic_card = _workshop_card_html(response, 'basic-card')
        self.assertIn('data-testid="workshop-tier-badge"', basic_card)
        self.assertIn('data-required-level="10"', basic_card)
        self.assertIn('data-lucide="lock"', basic_card)
        self.assertIn('Basic or above', basic_card)
        self.assertNotIn('data-testid="workshop-free-badge"', basic_card)
        self.assertIn(
            'Main or above', _workshop_card_html(response, 'main-card'),
        )
        self.assertIn(
            'Premium', _workshop_card_html(response, 'premium-card'),
        )

    def test_catalog_card_stays_at_book_card_density(self):
        # The unified card (shared content/_content_card.html) matches the
        # book card's clean density: badge + title + description + meta only.
        # Deliverable ("Includes") pills, tool chips, and topic chips do NOT
        # render on the card — they live on the workshop detail page.
        Workshop.objects.all().delete()
        complete = _make_workshop(
            slug='complete-signals',
            title='Complete Signals',
            with_event=True,
            materials=[{'title': 'Slides', 'url': 'https://example.com/slides'}],
            code_repo_url='https://github.com/example/workshop',
            tags=['agents'],
            core_tools=['Django'],
        )
        _make_page(complete, 'intro', 'Intro', 1)
        _make_workshop(
            slug='no-signals',
            title='No Signals',
            description='',
            instructor=None,
            tags=[],
            with_event=True,
            recording_url='',
            materials=[],
            code_repo_url='',
        )

        response = self.client.get(WORKSHOPS_CATALOG_URL)

        complete_card = _workshop_card_html(response, 'complete-signals')
        self.assertIn('data-testid="workshop-card-title"', complete_card)
        self.assertIn('Complete Signals', complete_card)
        self.assertNotIn('data-testid="workshop-card-deliverables"', complete_card)
        self.assertNotIn('data-testid="workshop-card-tools"', complete_card)
        self.assertNotIn('data-testid="workshop-card-topics"', complete_card)
        self.assertNotIn('data-testid="workshop-card-deliverable-pages"', complete_card)
        self.assertNotIn('data-testid="workshop-card-deliverable-recording"', complete_card)

        no_signals_card = _workshop_card_html(response, 'no-signals')
        self.assertIn('data-testid="workshop-card-link"', no_signals_card)
        self.assertIn('No Signals', no_signals_card)
        self.assertNotIn('data-testid="workshop-card-description"', no_signals_card)
        self.assertNotIn('data-testid="workshop-card-instructor"', no_signals_card)
        self.assertNotIn('data-testid="workshop-card-topics"', no_signals_card)
        self.assertNotIn('data-testid="workshop-card-deliverables"', no_signals_card)

    def test_catalog_card_is_fully_clickable_without_nested_anchor(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        card = _workshop_card_html(response, 'one')

        parser = _NestedAnchorParser()
        parser.feed(card)

        self.assertFalse(parser.found_nested_anchor)
        self.assertIn('data-testid="workshop-card-link"', card)
        self.assertIn('href="/workshops/one"', card)
        # The editorial row keeps one curated topic eyebrow, never raw tags.
        self.assertNotIn('data-testid="workshop-card-tags"', card)
        self.assertEqual(card.count('data-testid="workshop-card-topic"'), 1)
        self.assertIn('Production &amp; Apps', card)
        self.assertIn('data-testid="workshop-card-arrow"', card)
        self.assertIn('hidden sm:block', card)
        self.assertIn('style="right: 1.25rem;"', card)

    def test_catalog_links_to_landing(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        self.assertContains(response, 'href="/workshops/one"')

    def test_catalog_empty_state(self):
        Workshop.objects.all().delete()
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        self.assertNotContains(response, 'data-testid="workshops-landing"')
        self.assertContains(response, 'data-testid="workshop-catalog"')
        self.assertContains(response, 'data-testid="workshops-empty-state"')
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'data-empty-kind="fresh"')
        self.assertContains(response, 'No workshops published yet')

    def test_landing_empty_preview_uses_shared_empty_state(self):
        Workshop.objects.all().delete()
        response = self.client.get(WORKSHOPS_LANDING_URL)

        self.assertContains(response, 'data-testid="workshops-landing"')
        self.assertContains(response, 'data-testid="workshops-preview"')
        self.assertContains(response, 'No workshops published yet')

    def test_catalog_filter_by_tag(self):
        _make_workshop(slug='three', title='Other Topic', tags=['rust'])
        response = self.client.get(f'{WORKSHOPS_CATALOG_URL}?tag=rust')
        self.assertNotContains(response, 'data-testid="workshops-landing"')
        self.assertContains(response, 'data-testid="workshop-catalog"')
        self.assertContains(response, 'Other Topic')
        self.assertNotContains(response, 'Visible Workshop')
        # Legacy bounded tag URLs remain readable, but their granular tag
        # controls and active-filter chrome are retired from the public UI.
        self.assertNotContains(response, 'data-testid="workshop-facet-topic"')
        self.assertNotContains(response, 'data-testid="workshop-active-filters"')
        self.assertNotContains(response, 'data-testid="workshop-active-tag"')

    def test_catalog_multiple_legacy_tags_keep_and_semantics_without_controls(self):
        _make_workshop(
            slug='agents-rag',
            title='Agents RAG',
            tags=['agents', 'rag'],
        )
        _make_workshop(slug='rag-only', title='RAG Only', tags=['rag'])

        response = self.client.get(
            f'{WORKSHOPS_CATALOG_URL}?tag=agents&tag=rag',
        )

        self.assertEqual(response.context['selected_tags'], ['agents', 'rag'])
        self.assertContains(response, 'Agents RAG')
        self.assertNotContains(response, 'Visible Workshop')
        self.assertNotContains(response, 'RAG Only')
        self.assertNotContains(response, 'data-testid="workshop-active-tag"')
        self.assertNotContains(response, 'data-testid="workshop-facet-topic"')

    def test_catalog_card_uses_shared_editorial_row_container(self):
        response = self.client.get(f'{WORKSHOPS_CATALOG_URL}?access=paid')

        self.assertContains(response, 'data-testid="workshops-list"')
        self.assertContains(response, 'max-w-3xl')
        self.assertContains(response, 'data-testid="workshop-card-topic"')

        body = response.content.decode()
        article_start = body.index('data-testid="workshop-card"')
        article_open = _opening_tag_for_testid(
            body, 'article', 'workshop-card',
        )
        card_anchor_start = body.index('href="/workshops/one"', article_start)
        card_anchor_start = body.rfind('<a', article_start, card_anchor_start)
        card_anchor_open = body[
            card_anchor_start:body.index('>', card_anchor_start)
        ]
        # The shared owner switches from grid-card chrome to calm divider-led
        # editorial rows for the full archive.
        self.assertIn('border-b border-border/70', article_open)
        self.assertNotIn('rounded-lg border border-border', article_open)
        self.assertIn('flex flex-col gap-4 py-6', card_anchor_open)
        self.assertIn('sm:flex-row', card_anchor_open)
        self.assertNotIn('flex flex-1 flex-col', card_anchor_open)

        # The fully-clickable card has no nested anchors.
        card = _workshop_card_html(response, 'one')
        parser = _NestedAnchorParser()
        parser.feed(card)
        self.assertFalse(parser.found_nested_anchor)

    def test_catalog_filter_no_match_shows_empty_state(self):
        response = self.client.get(
            f'{WORKSHOPS_CATALOG_URL}?tag=does-not-exist',
        )
        self.assertNotContains(response, 'data-testid="workshops-landing"')
        self.assertContains(response, 'data-testid="workshop-catalog"')
        self.assertNotContains(response, 'data-testid="workshop-active-filters"')
        self.assertNotContains(response, 'data-testid="workshop-topic-summary"')
        self.assertContains(response, 'No workshops found')
        self.assertContains(response, 'data-testid="workshops-empty-state"')
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'data-empty-kind="filter"')
        self.assertContains(response, 'No workshops match the selected filters.')
        self.assertContains(response, 'href="/workshops/catalog"')
        self.assertContains(response, 'View all workshops')

    def test_catalog_route_ordering_does_not_treat_catalog_as_slug(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'All workshops')

        missing = self.client.get('/workshops/does-not-exist')
        self.assertEqual(missing.status_code, 404)

    def test_catalog_orders_published_workshops_newest_first(self):
        older = _make_workshop(
            slug='older',
            title='Older Workshop',
            tags=['history'],
        )
        older.date = date(2026, 1, 10)
        older.save()
        newer = _make_workshop(
            slug='newer',
            title='Newer Workshop',
            tags=['future'],
        )
        newer.date = date(2026, 7, 10)
        newer.save()
        draft_newer = _make_workshop(
            slug='draft-newer',
            title='Draft Newer Workshop',
            status='draft',
        )
        draft_newer.date = date(2026, 8, 10)
        draft_newer.save()

        response = self.client.get(WORKSHOPS_CATALOG_URL)
        body = response.content.decode()

        self.assertLess(
            body.index('Newer Workshop'),
            body.index('Visible Workshop'),
        )
        self.assertLess(
            body.index('Visible Workshop'),
            body.index('Older Workshop'),
        )
        self.assertNotContains(response, 'Draft Newer Workshop')

    def test_catalog_keeps_fresh_empty_state_when_no_published_workshops(self):
        Workshop.objects.all().delete()

        response = self.client.get(f'{WORKSHOPS_CATALOG_URL}?tag=agents')

        self.assertContains(response, 'data-testid="workshops-empty-state"')
        self.assertContains(response, 'data-empty-kind="fresh"')
        self.assertContains(response, 'No workshops published yet')
        self.assertNotContains(response, 'data-testid="workshop-active-filters"')
        self.assertNotContains(response, 'No workshops match the selected topics.')

    def test_catalog_does_not_add_workshops_listing_api_endpoint(self):
        response = self.client.get('/api/workshops')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            Workshop._meta.get_field('tags').get_internal_type(),
            'JSONField',
        )
        with self.assertRaises(LookupError):
            apps.get_model('content', 'Topic')

    def test_catalog_missing_cover_renders_no_preview_node(self):
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        card = _workshop_card_html(response, 'one')

        self.assertNotIn('data-testid="workshop-card-preview', card)
        self.assertNotIn('aspect-video', card)
        self.assertLess(
            card.index('data-testid="workshop-card-primary-signals"'),
            card.index('data-testid="workshop-card-title"'),
        )
        self.assertContains(response, 'group block focus-visible:outline-none')
        self.assertNotContains(response, 'h-12 w-12 text-muted-foreground')

    def test_catalog_auto_banner_only_renders_no_preview_node(self):
        auto_url = 'https://cdn.example/banners/generated-workshop.png'
        self.published.cover_image_url = ''
        self.published.custom_banner_url = ''
        self.published.auto_banner_url = auto_url
        self.published.save()

        response = self.client.get(WORKSHOPS_CATALOG_URL)
        card = _workshop_card_html(response, 'one')

        self.assertNotIn('data-testid="workshop-card-preview', card)
        self.assertNotIn('<img', card)
        self.assertNotIn(auto_url, card)

    def test_landing_embedded_catalog_auto_banner_only_renders_no_preview_node(self):
        auto_url = 'https://cdn.example/banners/landing-generated-workshop.png'
        self.published.cover_image_url = ''
        self.published.custom_banner_url = ''
        self.published.auto_banner_url = auto_url
        self.published.save()

        response = self.client.get(WORKSHOPS_LANDING_URL)
        card = _workshop_card_html(response, 'one')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-testid="workshop-card-preview', card)
        self.assertNotIn('<img', card)
        self.assertNotIn(auto_url, card)

    def test_catalog_cover_image_has_alt_text_and_lazy_loading(self):
        self.published.cover_image_url = 'https://cdn.example/workshop-card.png'
        self.published.custom_banner_url = 'https://cdn.example/custom/workshop-card.png'
        self.published.auto_banner_url = 'https://cdn.example/generated/workshop-card.png'
        self.published.save()
        response = self.client.get(WORKSHOPS_CATALOG_URL)
        card = _workshop_card_html(response, 'one')
        self.assertContains(response, 'data-testid="workshop-card-preview-image"')
        self.assertContains(response, 'https://cdn.example/workshop-card.png')
        self.assertContains(response, 'alt="Cover image for Visible Workshop"')
        self.assertContains(response, 'loading="lazy"')
        self.assertContains(
            response,
            'data-testid="workshop-card-preview-fallback" hidden',
        )
        self.assertIn('https://cdn.example/workshop-card.png', card)
        self.assertNotIn('https://cdn.example/custom/workshop-card.png', card)
        self.assertNotIn('https://cdn.example/generated/workshop-card.png', card)

    def test_catalog_custom_banner_preview_beats_auto_banner(self):
        custom_url = 'https://cdn.example/custom-banners/workshop-card.png'
        auto_url = 'https://cdn.example/banners/generated-workshop-card.png'
        self.published.cover_image_url = ''
        self.published.custom_banner_url = custom_url
        self.published.auto_banner_url = auto_url
        self.published.save()

        response = self.client.get(WORKSHOPS_CATALOG_URL)
        card = _workshop_card_html(response, 'one')

        self.assertIn('data-testid="workshop-card-preview-image"', card)
        self.assertIn(custom_url, card)
        self.assertNotIn(auto_url, card)
        self.assertIn('data-testid="workshop-card-preview-fallback" hidden', card)

    def test_filtered_catalog_keeps_auto_banner_only_card_text_first(self):
        Workshop.objects.all().delete()
        agents_auto_url = 'https://cdn.example/banners/agents-generated.png'
        _make_workshop(
            slug='agents-card',
            title='Agents Card',
            pages=LEVEL_OPEN,
            tags=['agents'],
            auto_banner_url=agents_auto_url,
        )
        _make_workshop(
            slug='python-card',
            title='Python Card',
            pages=LEVEL_OPEN,
            tags=['python'],
            cover_image_url='https://cdn.example/covers/python.png',
        )

        response = self.client.get(f'{WORKSHOPS_CATALOG_URL}?tag=agents')
        card = _workshop_card_html(response, 'agents-card')

        self.assertIn('Agents Card', card)
        self.assertNotIn('data-testid="workshop-card-preview', card)
        self.assertNotIn('<img', card)
        self.assertNotIn(agents_auto_url, card)
        self.assertNotContains(response, 'Python Card')

    def test_catalog_draft_metadata_does_not_leak_through_card_signals(self):
        draft = _make_workshop(
            slug='secret-draft',
            title='Secret Draft Workshop',
            status='draft',
            with_event=True,
            recording_url='https://example.com/secret-recording',
            materials=[{
                'title': 'Secret Event Slides',
                'url': 'https://example.com/secret-event-slides',
            }],
            code_repo_url='https://github.com/example/secret-draft',
            tags=['secret-draft-tag'],
            instructor='Secret Instructor',
        )
        draft.materials = [{
            'title': 'Secret Workbook',
            'url': 'https://example.com/secret-workbook',
        }]
        draft.save()
        _make_page(draft, 'secret-page', 'Secret Page', 1)

        response = self.client.get(WORKSHOPS_CATALOG_URL)

        self.assertNotContains(response, 'Secret Draft Workshop')
        self.assertNotContains(response, 'secret-draft-tag')
        self.assertNotContains(response, 'Secret Instructor')
        self.assertNotContains(response, 'Secret Event Slides')
        self.assertNotContains(response, 'Secret Workbook')
        self.assertNotContains(response, 'https://github.com/example/secret-draft')


class WorkshopLandingTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws',
            title='Production Agents',
            with_event=True,
            code_repo_url='https://github.com/org/repo',
            cover_image_url='https://cdn.example/cover.png',
            tags=['agents'],
        )
        cls.page1 = _make_page(cls.workshop, 'intro', 'Intro', 1)
        cls.page2 = _make_page(cls.workshop, 'setup', 'Setup', 2)
        cls.page3 = _make_page(cls.workshop, 'deploy', 'Deploy', 3)

        cls.user_free = User.objects.create_user(
            email='free@x.com', password='pw', tier=cls.free_tier,
        )
        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_landing_404_for_draft(self):
        Workshop.objects.create(
            slug='draft-ws', title='Draft', status='draft',
            date=date(2026, 4, 21),
        )
        response = self.client.get('/workshops/draft-ws')
        self.assertEqual(response.status_code, 404)

    def test_landing_404_for_unknown(self):
        response = self.client.get('/workshops/does-not-exist')
        self.assertEqual(response.status_code, 404)

    def test_landing_renders_seo_metadata(self):
        response = self.client.get('/workshops/ws')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Production Agents')
        self.assertContains(response, 'data-testid="workshop-title"')
        # description_html is rendered (markdown -> HTML)
        self.assertContains(response, '<h1>Hello</h1>')

    def test_landing_does_not_render_duplicate_preview_card(self):
        response = self.client.get('/workshops/ws')
        self.assertNotContains(response, 'data-testid="workshop-detail-preview"')
        self.assertNotContains(response, 'data-testid="workshop-detail-preview-image"')
        self.assertNotContains(response, 'data-testid="workshop-detail-preview-fallback"')

    def test_landing_missing_cover_does_not_render_decorative_preview(self):
        ws = _make_workshop(
            slug='no-cover',
            title='No Cover Workshop',
            cover_image_url='',
            tags=['agents'],
        )
        response = self.client.get(ws.get_absolute_url())
        self.assertContains(response, 'No Cover Workshop')
        self.assertNotContains(response, 'data-testid="workshop-detail-preview"')
        self.assertNotContains(response, 'data-testid="workshop-detail-preview-fallback"')

    def test_landing_shows_instructor_and_date(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'Alice')
        self.assertContains(response, 'April 21, 2026')

    def test_landing_shows_skill_level_and_standard_description(self):
        ws = _make_workshop(
            slug='skill-detail',
            title='Skill Detail',
            skill_level='intermediate',
        )

        response = self.client.get(ws.get_absolute_url())

        self.assertContains(response, 'data-testid="workshop-skill-level"')
        self.assertContains(response, 'Skill level: Intermediate')
        self.assertContains(response, 'modify code, connect APIs')

    def test_landing_shows_full_core_tools_when_landing_access_allowed(self):
        ws = _make_workshop(
            slug='tool-detail',
            title='Tool Detail',
            landing=LEVEL_OPEN,
            pages=LEVEL_OPEN,
            recording=LEVEL_OPEN,
            core_tools=['OpenAI API', 'Django', 'Claude Code'],
        )

        response = self.client.get(ws.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-detail-tools"')
        self.assertContains(response, 'Tools &amp; technologies')
        body = response.content.decode()
        tools_index = body.index('data-testid="workshop-detail-tools"')
        self.assertLess(
            body.index('OpenAI API', tools_index),
            body.index('Django', tools_index),
        )
        self.assertLess(
            body.index('Django', tools_index),
            body.index('Claude Code', tools_index),
        )

    def test_landing_paywall_does_not_leak_core_tools(self):
        ws = _make_workshop(
            slug='paid-tool-detail',
            title='Paid Tool Detail',
            landing=LEVEL_MAIN,
            pages=LEVEL_MAIN,
            recording=LEVEL_MAIN,
            core_tools=['Secret Internal Tool'],
        )

        response = self.client.get(ws.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-landing-paywall"')
        self.assertNotContains(response, 'data-testid="workshop-detail-tools"')
        self.assertNotContains(response, 'Secret Internal Tool')

    def test_landing_shows_code_repo_button(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-code-repo-link"')
        self.assertContains(response, 'https://github.com/org/repo')

    def test_landing_hides_code_repo_button_when_empty(self):
        ws = _make_workshop(slug='no-repo', title='No Repo')
        response = self.client.get(ws.get_absolute_url())
        self.assertNotContains(response, 'data-testid="workshop-code-repo-link"')

    def test_landing_anon_below_pages_gate_sees_paywall(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertContains(response, 'Upgrade to Basic to access this workshop')
        # The paywall renders a value manifest of what membership unlocks.
        self.assertContains(response, 'data-testid="gated-value-items"')
        self.assertContains(response, 'Step-by-step tutorial')
        self.assertNotContains(response, 'public metadata')
        # Issue #481: paywall pill reads "Basic or above required".
        self.assertContains(response, 'Basic or above required')
        self.assertNotContains(response, 'Basic+ required')
        self.assertNotContains(response, 'data-testid="gated-current-state"')

    def test_landing_anon_on_registered_default_sees_signin_paywall(self):
        """Issue #571 PM fix: anonymous on a workshop using the new
        ``pages_required_level=LEVEL_REGISTERED`` (5) default must see
        Sign-In-shaped copy on the landing pages paywall — not the
        nonsensical "Upgrade to Free" / "/membership" combo.
        """
        ws = _make_workshop(
            slug='reg-ws', title='Registered Workshop',
            landing=0, pages=5, recording=20,
        )
        _make_page(ws, 'intro', 'Intro', 1)
        response = self.client.get(ws.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        # Card still renders — the paywall is just reshaped.
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        # Sign-In-shaped heading and CTAs.
        self.assertContains(response, 'Sign in to access this workshop')
        # Free-with-sign-in badge, no tier pill.
        self.assertContains(response, 'data-testid="gated-free-badge"')
        self.assertNotContains(response, 'data-testid="gated-required-tier"')
        # Sign-in companion link with next= preserved.
        self.assertContains(
            response,
            '/accounts/login/?next=%2Fworkshops%2Freg-ws',
        )
        self.assertContains(response, 'Sign in')
        # The signup action stack renders a "Create a free account" button
        # (no inline register form/card) pointing at the register/signup page.
        self.assertContains(response, 'data-testid="teaser-signup-cta"')
        self.assertContains(response, 'Create a free account')
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertContains(
            response,
            '/accounts/signup/?next=%2Fworkshops%2Freg-ws',
        )
        # The broken "Upgrade to Free" copy and the /membership CTA must be
        # gone on this surface — the visitor just needs to authenticate.
        self.assertNotContains(response, 'Upgrade to Free')
        body = response.content.decode()
        # Scope to the paywall card and assert its actions point at auth
        # (login/signup), never /membership.
        card_start = body.index('data-testid="workshop-pages-paywall"')
        card_end = body.index('</div>', body.index(
            'data-testid="signup-actions"', card_start,
        ))
        card_slice = body[card_start:card_end + 6]
        self.assertIn('/accounts/login/?next=', card_slice)
        self.assertNotIn('href="/membership"', card_slice)
        self.assertNotIn(
            'href="/membership"', card_slice,
            'pages paywall must not link to /membership for anonymous '
            'visitors on the registered-default wall',
        )
        self.assertNotContains(response, 'Free required')
        self.assertNotContains(
            response, 'data-testid="gated-required-tier"',
        )

    def test_landing_free_unverified_open_pages_render_no_verify_gate(self):
        # Issue #1318: an open (level 0) workshop landing + pages render for
        # an unverified free user — signing up must never reduce access
        # below anonymous. Flipped from asserting the verify-email gate.
        ws = _make_workshop(
            slug='free-unverified-pages',
            title='Free Unverified Pages Workshop',
            landing=0,
            pages=0,
            recording=20,
        )
        _make_page(ws, 'intro', 'Intro', 1)
        user = User.objects.create_user(
            email='free-unverified-pages@example.com',
            password='pw',
            tier=self.free_tier,
            email_verified=False,
        )
        self.client.force_login(user)

        response = self.client.get(ws.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="verify-email-required-card"')
        self.assertNotContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertNotContains(response, 'Upgrade to Free')
        # The open pages list renders — the user has full access.
        self.assertContains(response, 'Intro')

    def test_landing_free_unverified_open_landing_paid_pages_paywall(self):
        # Issue #1318: with an open (level 0) landing and Basic-gated pages,
        # an unverified free user sees the landing (no verify card) and the
        # tier paywall on the pages section — verification is not the
        # blocker, tier is.
        ws = _make_workshop(
            slug='free-unverified-landing',
            title='Free Unverified Landing Workshop',
            landing=0,
            pages=10,
            recording=20,
        )
        user = User.objects.create_user(
            email='free-unverified-landing@example.com',
            password='pw',
            tier=self.free_tier,
            email_verified=False,
        )
        self.client.force_login(user)

        response = self.client.get(ws.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="verify-email-required-card"')
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertContains(response, 'Upgrade to Basic to access this workshop')

    def test_landing_basic_user_does_not_see_pages_paywall(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws')
        self.assertNotContains(response, 'data-testid="workshop-pages-paywall"')

    def test_landing_lists_all_pages_in_order(self):
        response = self.client.get('/workshops/ws')
        body = response.content.decode()
        i_intro = body.index('Intro')
        i_setup = body.index('Setup')
        i_deploy = body.index('Deploy')
        self.assertLess(i_intro, i_setup)
        self.assertLess(i_setup, i_deploy)

    def test_landing_page_rows_show_lock_when_gated(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(
            response, 'data-testid="workshop-page-lock-icon"', count=3,
        )

    def test_landing_page_rows_link_to_tutorial(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, '/workshops/ws/tutorial/intro')
        self.assertContains(response, '/workshops/ws/tutorial/setup')
        self.assertContains(response, 'min-h-[44px]')
        self.assertContains(response, 'focus-visible:ring-2')

    def test_landing_video_card_shows_recording_tier_when_gated(self):
        # Basic user passes pages but not recording (level 20)
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-video-locked"')

    # Issue #844: the "Watch the recording" card must only render when a
    # recording actually exists on the linked event. Existence-gating
    # (does a recording exist?) is independent of access-gating (does the
    # viewer's tier clear the recording level?).

    def test_landing_video_card_renders_when_recording_available(self):
        # Main user clears the recording gate (level 20) and the default
        # fixture workshop has a linked event with a recording_url.
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-video-link"')
        self.assertContains(response, 'Watch the recording')

    def test_landing_video_card_shows_full_video_copy_when_unlocked(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws')
        self.assertContains(
            response,
            'Full workshop video with timestamps and downloadable materials.',
        )

    def test_landing_video_card_omitted_when_no_event(self):
        # No linked event -> no recording -> card must be absent even for a
        # user who would otherwise clear the recording gate.
        ws = _make_workshop(
            slug='no-rec-evt', title='No Recording Event',
            with_event=False, landing=0, pages=0, recording=0,
        )
        self.client.force_login(self.user_main)
        response = self.client.get(ws.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="workshop-video-link"')
        self.assertNotContains(response, 'Watch the recording')
        self.assertNotContains(
            response,
            'Full workshop video with timestamps and downloadable materials.',
        )

    def test_landing_video_card_omitted_when_event_has_no_recording(self):
        # Linked event but all recording URLs empty -> has_recording False.
        ws = _make_workshop(
            slug='evt-no-rec', title='Event No Recording',
            with_event=True, recording_url='', landing=0, pages=0, recording=0,
        )
        self.client.force_login(self.user_main)
        response = self.client.get(ws.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="workshop-video-link"')

    def test_landing_video_card_omitted_keeps_code_repo_link(self):
        # Hiding the recording card must not drop the GitHub repo link.
        ws = _make_workshop(
            slug='no-rec-repo', title='No Recording But Repo',
            with_event=False, landing=0, pages=0, recording=0,
            code_repo_url='https://github.com/org/repo',
        )
        response = self.client.get(ws.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="workshop-video-link"')
        self.assertContains(response, 'data-testid="workshop-code-repo-link"')

    def test_landing_video_card_renders_locked_when_gated_but_recording_exists(self):
        # Existence-gating and access-gating are independent: a recording
        # exists, so the card renders, but an anonymous visitor who clears
        # the open landing/pages gates still fails the level-20 recording
        # gate and sees the locked pill.
        ws = _make_workshop(
            slug='gated-rec', title='Gated Recording',
            with_event=True, landing=0, pages=0, recording=20,
        )
        response = self.client.get(ws.get_absolute_url())
        self.assertContains(response, 'data-testid="workshop-video-link"')
        self.assertContains(response, 'data-testid="workshop-video-locked"')

    def test_landing_event_cross_link_hidden_when_event_exists(self):
        response = self.client.get('/workshops/ws')
        self.assertNotContains(
            response, 'data-testid="workshop-event-cross-link"',
        )
        self.assertNotContains(
            response, 'Saw this on the events timeline? View the event page',
        )

    def test_landing_event_cross_link_hidden_when_no_event(self):
        ws = _make_workshop(slug='no-evt', title='No Event')
        response = self.client.get(ws.get_absolute_url())
        self.assertNotContains(
            response, 'data-testid="workshop-event-cross-link"',
        )

    def test_landing_landing_paywall_replaces_everything_when_landing_gated(self):
        ws = _make_workshop(
            slug='lg', title='Landing-gated',
            landing=10, pages=10, recording=20,
            with_event=True,
            materials=[
                {'title': 'Slides', 'url': 'https://x/slides.pdf'},
            ],
            code_repo_url='https://github.com/org/private-repo',
            tags=['agents'],
        )
        _make_page(ws, 'one', 'One', 1)
        # Anon user fails landing gate
        response = self.client.get(ws.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Landing-gated')
        self.assertContains(response, 'April 21, 2026')
        self.assertContains(response, 'Alice')
        self.assertContains(response, 'agents')
        self.assertContains(response, 'data-testid="workshop-landing-paywall"')
        self.assertContains(response, 'Upgrade to Basic to view this workshop')
        self.assertContains(
            response,
            'Membership unlocks the workshop description, tutorial pages, '
            'recording details, and materials when available.',
        )
        self.assertNotContains(response, 'public metadata')
        # Issue #481: paywall pill uses "Basic or above required".
        self.assertContains(response, 'Basic or above required')
        # Description body is hidden
        self.assertNotContains(response, 'data-testid="workshop-description"')
        self.assertNotContains(response, 'data-testid="workshop-materials"')
        # Pages list is hidden
        self.assertNotContains(response, 'data-testid="workshop-pages-list"')
        self.assertNotContains(response, 'data-testid="workshop-actions"')
        self.assertNotContains(response, 'data-testid="workshop-video-link"')
        self.assertNotContains(response, 'data-testid="workshop-code-repo-link"')

    def test_landing_premium_pages_paywall_drops_or_above(self):
        """Issue #481 AC: Premium-only paywall says "Premium required".

        Premium is the highest public tier so the paywall pill must NOT
        say "Premium or above required" — there is no higher public tier
        to upgrade to.
        """
        ws = _make_workshop(
            slug='premium-ws', title='Premium Only',
            landing=0, pages=30, recording=30,
        )
        _make_page(ws, 'one', 'One', 1)
        response = self.client.get(ws.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertContains(response, 'Premium required')
        self.assertNotContains(response, 'Premium or above required')
        self.assertNotContains(response, 'Premium+')

    def test_landing_emits_workshop_jsonld(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, '"@type": "Course"')
        self.assertContains(response, '"name": "Production Agents"')

    def test_landing_emits_og_tags(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'Production Agents')


class WorkshopVideoTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws',
            title='Recording Workshop',
            with_event=True,
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[
                {'title': 'Slides', 'url': 'https://x/slides.pdf', 'type': 'pdf'},
            ],
        )
        cls.workshop.event.timestamps = [
            {'time_seconds': 0, 'label': 'Intro'},
        ]
        cls.workshop.event.transcript_text = 'Workshop transcript text.'
        cls.workshop.event.save()
        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_video_404_for_draft(self):
        ws = Workshop.objects.create(
            slug='dft', title='dft', status='draft',
            date=date(2026, 4, 21),
        )
        response = self.client.get(f'{ws.get_absolute_url()}/video')
        self.assertEqual(response.status_code, 404)

    def test_video_anon_below_pages_sees_recording_paywall(self):
        # Default workshop: landing=0, pages=10, recording=20.
        # Anon (level 0) passes landing only — so the recording-tier
        # paywall renders (anon is below pages too, but the recording
        # gate is what matters on the video page). Issue #515 returns 403
        # to mirror the course-unit teaser pattern.
        response = self.client.get('/workshops/ws/video')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="video-paywall"', status_code=403,
        )
        self.assertContains(
            response, 'Upgrade to Main to watch the recording',
            status_code=403,
        )

    def test_video_basic_below_recording_sees_paywall(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/video')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="video-paywall"', status_code=403,
        )
        self.assertContains(
            response,
            'Unlock the full recording, timestamps, and downloadable '
            'materials with a membership.',
            status_code=403,
        )
        self.assertNotContains(
            response, 'public metadata', status_code=403,
        )
        # Issue #481: pill reads "Main or above required".
        self.assertContains(
            response, 'Main or above required', status_code=403,
        )
        self.assertNotContains(
            response, 'Main+ required', status_code=403,
        )
        self.assertContains(
            response, 'Current access: Basic member', status_code=403,
        )

    def test_video_main_renders_player(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws/video')
        self.assertNotContains(response, 'data-testid="video-paywall"')
        self.assertContains(response, 'data-testid="video-player"')
        self.assertTemplateUsed(response, 'events/_recording_embed.html')
        self.assertContains(response, 'data-testid="video-chapters"')
        self.assertContains(response, 'class="video-timestamp')
        self.assertContains(response, 'data-time-seconds="0"')
        self.assertContains(response, 'data-source="youtube"')

    def test_video_main_renders_materials(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws/video')
        self.assertTemplateUsed(response, 'events/_recording_materials.html')
        self.assertContains(response, 'data-testid="video-materials"')
        self.assertContains(response, 'Slides')

    def test_video_main_renders_transcript(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws/video')
        self.assertTemplateUsed(response, 'events/_recording_transcript.html')
        self.assertContains(response, 'data-testid="video-transcript"')
        self.assertContains(response, 'Workshop transcript text.')

    def test_video_landing_paywall_when_landing_gated(self):
        ws = _make_workshop(
            slug='lg', title='Landing gated',
            landing=20, pages=20, recording=20, with_event=True,
        )
        # Basic user fails landing gate (level 10 < 20)
        u = User.objects.create_user(
            email='b2@x.com', password='pw', tier=self.basic_tier,
        )
        self.client.force_login(u)
        response = self.client.get(f'{ws.get_absolute_url()}/video')
        self.assertContains(response, 'data-testid="video-landing-paywall"')
        self.assertContains(response, 'Current access: Basic member')


class WorkshopPageDetailTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws', title='Tutorial Workshop',
        )
        cls.p1 = _make_page(
            cls.workshop, 'one', 'One', 1, body='# First page',
        )
        cls.p2 = _make_page(
            cls.workshop, 'two', 'Two', 2, body='Second page body',
        )
        cls.p3 = _make_page(cls.workshop, 'three', 'Three', 3)

        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )

    def test_page_404_for_draft_workshop(self):
        ws = Workshop.objects.create(
            slug='dft', title='dft', status='draft',
            date=date(2026, 4, 21),
        )
        WorkshopPage.objects.create(
            workshop=ws, slug='one', title='One', sort_order=1, body='x',
        )
        response = self.client.get(f'{ws.get_absolute_url()}/tutorial/one')
        self.assertEqual(response.status_code, 404)

    def test_page_404_for_unknown_page(self):
        response = self.client.get('/workshops/ws/tutorial/nope')
        self.assertEqual(response.status_code, 404)

    def test_page_anon_returns_403_with_paywall(self):
        # Issue #515 ports the course-unit teaser pattern: gated tutorial
        # pages now return 403 (mirroring course units) and render the
        # title, breadcrumb, ~150-word teaser body, and paywall card.
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="page-title"', status_code=403,
        )
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertContains(
            response, 'Upgrade to Basic to access this workshop',
            status_code=403,
        )
        self.assertContains(
            response,
            'membership unlocks the tutorial body.',
            status_code=403,
        )
        self.assertNotContains(
            response, 'public metadata', status_code=403,
        )
        # Issue #481: paywall pill reads "Basic or above required".
        self.assertContains(
            response, 'Basic or above required', status_code=403,
        )
        self.assertNotContains(
            response, 'Basic+ required', status_code=403,
        )
        self.assertNotContains(
            response, 'data-testid="gated-current-state"', status_code=403,
        )
        # Full body must NOT render
        self.assertNotContains(
            response, 'data-testid="page-body"', status_code=403,
        )

    def test_page_free_member_sees_current_access_state(self):
        user_free = User.objects.create_user(
            email='free-page@x.com', password='pw', tier=self.free_tier,
        )
        self.client.force_login(user_free)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertContains(
            response, 'Current access: Free member', status_code=403,
        )

    def test_page_basic_renders_body(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertContains(response, 'data-testid="page-body"')
        self.assertContains(response, '<h1>First page</h1>')

    def test_page_breadcrumb_links_to_landing(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertContains(response, 'data-testid="page-breadcrumb"')
        self.assertContains(response, 'href="/workshops/ws"')

    def test_page_first_page_has_no_prev(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertNotContains(response, 'data-testid="page-prev-btn"')
        self.assertContains(response, 'data-testid="page-next-btn"')

    def test_page_middle_page_has_both(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/two')
        self.assertContains(response, 'data-testid="page-prev-btn"')
        self.assertContains(response, 'data-testid="page-next-btn"')

    def test_page_last_page_has_no_next(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/three')
        self.assertContains(response, 'data-testid="page-prev-btn"')
        self.assertNotContains(response, 'data-testid="page-next-btn"')

    def test_page_sidebar_highlights_current(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/two')
        self.assertContains(response, 'data-testid="sidebar-current-page"')
        # The 'aria-current="page"' attribute is rendered on the active row
        self.assertContains(response, 'aria-current="page"')


class PublicGatedContentCopyRegressionTest(SimpleTestCase):
    def test_blog_and_tutorials_use_canonical_gated_card(self):
        # Issue #1335: blog_detail and tutorial_detail migrated off the
        # deprecated includes/content_gated.html onto the canonical
        # content/_gated_access_card.html; the legacy partial was deleted.
        repo_root = Path(__file__).resolve().parents[2]
        legacy_partial = (
            repo_root / 'templates' / 'includes' / 'content_gated.html'
        )

        self.assertFalse(legacy_partial.exists())
        for template_name in ('blog_detail.html', 'tutorial_detail.html'):
            template_source = (
                repo_root / 'templates' / 'content' / template_name
            ).read_text()
            self.assertNotIn('includes/content_gated.html', template_source)
            self.assertIn(
                'content/_gated_access_card.html', template_source,
            )

    def test_public_gated_templates_do_not_use_public_metadata_jargon(self):
        repo_root = Path(__file__).resolve().parents[2]
        template_paths = [
            *(repo_root / 'templates' / 'content').glob('*.html'),
            *(repo_root / 'templates' / 'content').glob('*/*.html'),
        ]

        offenders = [
            str(path.relative_to(repo_root))
            for path in template_paths
            if 'public metadata' in path.read_text().lower()
        ]

        self.assertEqual(offenders, [])


class LegacyDatedWorkshopUrlRedirectsTest(TierSetupMixin, TestCase):
    """Issue #1064 — valid dated workshop URLs redirect to slug-only URLs."""
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='legacy-ws', title='Legacy Workshop',
        )
        cls.canonical = '/workshops/legacy-ws'
        cls.legacy = '/workshops/2026-04-21-legacy-ws'
        cls.page = _make_page(
            cls.workshop, 'starting-notebook', 'Starting Notebook', 1,
        )
        cls.user_basic = User.objects.create_user(
            email='legacy-basic@x.com', password='pw', tier=cls.basic_tier,
        )

    def test_slug_only_landing_for_published_workshop_renders(self):
        response = self.client.get('/workshops/legacy-ws')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Legacy Workshop')

    def test_dated_landing_for_published_workshop_redirects(self):
        response = self.client.get(self.legacy)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.canonical)

    def test_dated_video_for_published_workshop_redirects_with_query(self):
        response = self.client.get(f'{self.legacy}/video?t=16:00')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], f'{self.canonical}/video?t=16:00')

    def test_dated_tutorial_for_published_workshop_redirects(self):
        response = self.client.get(
            f'{self.legacy}/tutorial/starting-notebook',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'{self.canonical}/tutorial/starting-notebook',
        )

    def test_date_mismatched_dated_url_404s(self):
        response = self.client.get('/workshops/2026-04-22-legacy-ws')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_draft_dated_url_404s(self):
        _make_workshop(slug='draft-legacy', title='Draft', status='draft')
        response = self.client.get('/workshops/2026-04-21-draft-legacy')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_bare_slug_landing_for_unknown_workshop_404s(self):
        response = self.client.get('/workshops/missing-workshop')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_bare_slug_tutorial_for_unknown_workshop_404s(self):
        response = self.client.get(
            '/workshops/missing-workshop/tutorial/starting-notebook',
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_canonical_landing_still_renders(self):
        response = self.client.get(self.canonical)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Legacy Workshop')

    def test_canonical_tutorial_page_renders_directly(self):
        # Anonymous user fails the default pages gate (Basic+); issue #515
        # returns 403 with the teaser layout. The point of the test is
        # that the canonical URL still serves a gated render (no redirect).
        response = self.client.get(
            f'{self.canonical}/tutorial/starting-notebook',
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Location', response)


class WorkshopPagePerPageOverrideViewTest(TierSetupMixin, TestCase):
    """View-level tests for the per-page ``required_level`` override (#571).

    Each test exercises one acceptance-criterion path end-to-end through
    ``workshop_page_detail`` / ``api_workshop_page_complete`` so the
    override actually drives the rendered template and API gate. Model
    semantics are covered separately in ``test_workshops.py``.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Workshop default is LEVEL_REGISTERED (5) so unauthenticated
        # visitors are blocked from inheriting pages, but a free verified
        # user passes.
        cls.workshop = _make_workshop(
            slug='gated-ws', title='Gated Workshop',
            landing=0, pages=5, recording=20,
        )
        cls.page_open = _make_page(
            cls.workshop, 'intro', 'Intro', 1,
            body='# Intro\n\nOpen body content.',
        )
        cls.page_open.required_level = 0  # open override
        cls.page_open.save()
        cls.page_inherited = _make_page(
            cls.workshop, 'deep-dive', 'Deep Dive', 2,
            body='# Deep Dive\n\nInherited body content.',
        )
        # Basic-gated workshop for the "free member on paid wall" path.
        cls.workshop_basic = _make_workshop(
            slug='basic-ws', title='Basic Workshop',
            landing=0, pages=10, recording=20,
        )
        cls.page_basic_inherits = _make_page(
            cls.workshop_basic, 'lesson', 'Lesson', 1,
            body='# Lesson\n\nBasic-required body.',
        )
        cls.user_free = User.objects.create_user(
            email='per-page-free@example.com', password='pw',
            tier=cls.free_tier, email_verified=True,
        )

    def test_anonymous_on_open_override_sees_full_body(self):
        # Page-level open override beats the workshop-default LEVEL_REGISTERED.
        response = self.client.get('/workshops/gated-ws/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="page-body"')
        self.assertContains(response, 'Open body content.')
        self.assertNotContains(response, 'data-testid="page-paywall"')

    def test_anonymous_on_inherited_page_sees_signin_paywall(self):
        # No override → inherits workshop's pages_required_level=5 →
        # anonymous gets the registration wall (Sign In CTA).
        response = self.client.get('/workshops/gated-ws/tutorial/deep-dive')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertContains(response, 'Sign in', status_code=403)
        # CTA preserves the return URL (URL-encoded in href).
        self.assertContains(
            response,
            '/accounts/login/?next=%2Fworkshops%2Fgated-ws%2F'
            'tutorial%2Fdeep-dive',
            status_code=403,
        )
        # Anonymous on a registration wall also gets the "Create a free
        # account" companion link.
        self.assertContains(
            response, 'Create a free account', status_code=403,
        )

    def test_free_member_on_registered_inherited_page_sees_body(self):
        # Workshop default 5 (registered) — a verified free user passes.
        self.client.force_login(self.user_free)
        response = self.client.get('/workshops/gated-ws/tutorial/deep-dive')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="page-body"')
        self.assertContains(response, 'Inherited body content.')

    def test_free_member_on_basic_inherited_page_sees_upgrade(self):
        # Workshop default 10 (Basic) and no override → free user gets
        # the upgrade-to-Basic CTA.
        self.client.force_login(self.user_free)
        response = self.client.get('/workshops/basic-ws/tutorial/lesson')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'Upgrade to Basic to access this workshop',
            status_code=403,
        )

    def test_api_complete_anonymous_returns_401(self):
        # Anonymous never reaches the access check — the 401 path runs first.
        response = self.client.post(
            '/api/workshops/gated-ws/pages/intro/complete',
        )
        self.assertEqual(response.status_code, 401)

    def test_api_complete_free_user_on_open_page_returns_200(self):
        # Free user on a page with required_level=0 succeeds — the per-page
        # override beats the workshop-wide gate (which is 5/registered).
        self.client.force_login(self.user_free)
        response = self.client.post(
            '/api/workshops/gated-ws/pages/intro/complete',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'completed': True})

    def test_landing_unchanged_for_anonymous(self):
        # Per-page gating must not bleed into the landing page; anonymous
        # still sees the landing (since landing_required_level=0).
        response = self.client.get('/workshops/gated-ws')
        self.assertEqual(response.status_code, 200)
        # Description and pages list rendered as usual.
        self.assertContains(response, 'Gated Workshop')

    def test_draft_workshop_page_stays_404(self):
        # Issue #750: draft workshops can't be reached via the legacy
        # tutorial URL either (the legacy redirect only matches published
        # workshops; an unmatched legacy URL falls through to 404).
        draft = _make_workshop(
            slug='draft-legacy', title='Draft Legacy', status='draft',
        )
        _make_page(draft, 'starting-notebook', 'Starting Notebook', 1)
        response = self.client.get(
            '/workshops/draft-legacy/tutorial/starting-notebook',
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_reserved_tutorial_child_path_stays_404(self):
        # ``/workshops/<slug>/tutorial`` (no page slug) doesn't match
        # any URL pattern — the bare ``/tutorial`` suffix isn't a slug
        # and there's no slug-only route below the legacy patterns.
        _make_page(self.workshop, 'tutorial', 'Reserved Tutorial', 2)
        response = self.client.get('/workshops/legacy-ws/tutorial')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)


class EventWorkshopCrossLinksTest(TierSetupMixin, TestCase):
    """Past-event card and event-detail cross-links to /workshops."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws', title='WriteUp Workshop',
            with_event=True, landing=0, pages=0, recording=0,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_events_past_card_redirects_to_workshop(self):
        """When an event has kind='workshop' and a linked Workshop, the
        past card links to /workshops/<slug> not /events/<slug>."""
        response = self.client.get('/events?filter=past')
        self.assertContains(
            response, 'data-testid="past-card-workshop-link"',
        )
        self.assertContains(response, 'href="/workshops/ws"')

    def test_events_past_card_shows_workshop_badge(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(
            response, 'data-testid="past-card-workshop-badge"',
        )

    def test_event_detail_shows_workshop_writeup_card(self):
        response = self.client.get(self.workshop.event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="event-workshop-writeup"',
        )
        self.assertContains(
            response, 'data-testid="event-workshop-writeup-link"',
        )
        self.assertContains(response, 'href="/workshops/ws"')

    def test_orphan_workshop_event_links_back_to_event(self):
        """Event with kind='workshop' but no linked Workshop falls back to
        the canonical id+slug event URL so we don't 404. Issue #673:
        ``Event.get_absolute_url`` is the single source of truth for
        that URL shape.
        """
        from datetime import timedelta
        now = timezone.now()
        orphan = Event.objects.create(
            slug='orphan-ws',
            title='Orphan',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            kind='workshop',
            recording_url='https://x/y',
            published=True,
        )
        response = self.client.get('/events?filter=past')
        # Standard event link form on the orphan card.
        self.assertContains(
            response, f'href="{orphan.get_absolute_url()}"',
        )

    def test_event_detail_no_writeup_for_standard_event(self):
        std = Event.objects.create(
            slug='std',
            title='Standard',
            start_datetime=timezone.now(),
            status='completed',
            kind='standard',
            recording_url='https://x/y',
            published=True,
        )
        response = self.client.get(std.get_absolute_url())
        self.assertNotContains(
            response, 'data-testid="event-workshop-writeup"',
        )


class WorkshopSitemapTest(TierSetupMixin, TestCase):
    """Sitemap exposes published workshops and their pages."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws-pub', title='Pub WS',
        )
        _make_page(cls.workshop, 'page-one', 'Page One', 1)
        cls.draft = _make_workshop(
            slug='ws-draft', title='Draft WS', status='draft',
        )
        _make_page(cls.draft, 'hidden-page', 'Hidden Page', 1)

    def test_sitemap_contains_published_workshop_landing(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/workshops/ws-pub')
        self.assertNotContains(
            response,
            '<loc>https://aishippinglabs.com/workshops/2026-04-21-ws-pub</loc>',
        )

    def test_sitemap_contains_published_workshop_page(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(
            response, '/workshops/ws-pub/tutorial/page-one',
        )
        self.assertNotContains(
            response, '/workshops/2026-04-21-ws-pub/tutorial/page-one',
        )

    def test_sitemap_excludes_draft_workshop(self):
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(response, '/workshops/ws-draft')
        self.assertNotContains(response, '/workshops/ws-draft')

    def test_sitemap_excludes_draft_workshop_pages(self):
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(response, 'hidden-page')


class WorkshopPageGetAbsoluteUrlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ws = Workshop.objects.create(
            slug='abs-url',
            title='Abs URL',
            date=date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.ws, slug='page', title='P', sort_order=1, body='x',
        )

    def test_workshop_page_get_absolute_url(self):
        self.assertEqual(
            self.page.get_absolute_url(),
            '/workshops/abs-url/tutorial/page',
        )


# ── Inline register card on workshop pages paywall (issue #652) ────────


class WorkshopPagesPaywallInlineRegisterTest(TierSetupMixin, TestCase):
    """Anonymous visitors on a workshop with the registered-default
    pages gate see the inline register card in the pages paywall slot
    (in place of the legacy "Create a free account" link). Issue #652.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # ``pages_required_level=5`` (LEVEL_REGISTERED) is the registered
        # wall — anonymous users get the Sign-In CTA + the inline register
        # card; logged-in free users get through to the pages.
        cls.workshop = _make_workshop(
            slug='anon-pages',
            title='Anon Pages Workshop',
            landing=0,
            pages=5,
            recording=20,
        )
        _make_page(cls.workshop, 'intro', 'Intro', 1)

    def test_anonymous_pages_paywall_shows_signup_actions(self):
        response = self.client.get('/workshops/anon-pages')
        self.assertEqual(response.status_code, 200)
        # Paywall card is rendered.
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        # The free-with-sign-in branch renders the shared signup-actions stack
        # (OAuth-first buttons + a "Create a free account" button + a sign-in
        # link), not an inline register form.
        self.assertContains(response, 'data-testid="signup-actions"')
        self.assertContains(response, 'data-testid="teaser-signup-cta"')
        self.assertContains(response, 'data-testid="teaser-signin-cta"')
        self.assertContains(response, 'Create a free account')
        # The retired inline register card / email input are gone.
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertNotContains(response, 'id="register-email"')
        # Sign-in link carries the workshop ?next= (URL-encoded in href).
        self.assertContains(
            response,
            '/accounts/login/?next=%2Fworkshops%2Fanon-pages',
        )

    def test_anonymous_pages_paywall_no_inline_register_js(self):
        """Issue #1343 regression: the workshop pages paywall now renders
        the shared ``_signup_actions.html`` stack (OAuth buttons + "Create a
        free account" + "Sign in"), which has no ``id="register-form"`` for
        ``inline-register.js`` to bind. The dead script block (and its
        ``auth-next-url`` payload / ``auth-helpers.js``) must not come back."""
        response = self.client.get('/workshops/anon-pages')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '/static/js/accounts/inline-register.js')
        self.assertNotContains(response, '/static/js/accounts/auth-helpers.js')
        self.assertNotContains(response, 'auth-next-url')
        # Guard against Django comment leaks — multi-line ``{# #}``
        # tags don't terminate so they leak into rendered HTML.
        self.assertNotContains(response, '{# ')

    def test_authenticated_user_does_not_see_inline_form(self):
        """A logged-in free user passes the registered wall and never
        sees the pages paywall (and therefore no inline card)."""
        User = get_user_model()
        user = User.objects.create_user(
            email='reg@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.email_verified = True
        user.save(update_fields=['tier', 'email_verified'])
        self.client.force_login(user)
        response = self.client.get('/workshops/anon-pages')
        self.assertNotContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertNotContains(response, 'data-testid="inline-register-card"')

    def test_anonymous_pages_paywall_uses_expanded_variant_not_compact(self):
        """Issue #654 regression: the workshop pages paywall keeps the
        expanded inline register variant — the paywall card is wide
        enough to absorb the OAuth divider + provider buttons inline,
        and only /membership tucks them behind a toggle.
        """
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        app = SocialApp.objects.create(
            provider='google', name='Google',
            client_id='google-cid', secret='google-secret',
        )
        app.sites.add(Site.objects.get_current())
        response = self.client.get('/workshops/anon-pages')
        self.assertEqual(response.status_code, 200)
        # OAuth visible without clicking a toggle.
        self.assertContains(response, 'Sign up with Google')
        self.assertNotContains(
            response, 'data-testid="inline-register-oauth-toggle"',
        )
        self.assertNotContains(
            response, 'data-testid="inline-register-oauth-disclosure"',
        )
        self.assertNotContains(response, 'More sign-in options')
