"""Issue #1339 — the shared content-card container and info-card class string.

The unified card system layers two new owners on the existing
``_clickable_card_classes.html`` anchor contract:

- ``content/_content_card.html`` renders the clickable-card CONTAINER (one
  anchor, the canonical article class string, and the single top-right arrow).
- ``content/_info_card_classes.html`` emits the static-card class string with no
  ``group``, no hover, and no anchor.

These template tests assert the container's structural contract directly, then
Phase 1 home/testimonial migrations assert the role changes on real pages.
"""

from __future__ import annotations

import datetime
from html.parser import HTMLParser
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from content.models import Article, Workshop

CANONICAL_ARROW = 'data-lucide="arrow-right"'
ARROW_MOBILE_RULE = "hidden sm:block"


def _count_anchors(html: str) -> int:
    class _Counter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.count = 0

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.count += 1

    parser = _Counter()
    parser.feed(html)
    return parser.count


def _render_card(**overrides) -> str:
    context = {
        "card_url": "/blog/example",
        "card_testid": "unit-card",
        "card_body_template": "content/_home_blog_card_body.html",
        "article": SimpleNamespace(
            title="Example title",
            description="Example description",
            short_date="Jan 1, 2026",
            reading_time="5 min read",
            tags=["ai"],
        ),
    }
    context.update(overrides)
    return render_to_string("content/_content_card.html", context)


class ContentCardContainerTest(SimpleTestCase):
    def test_renders_exactly_one_anchor_to_card_url(self):
        html = _render_card()
        self.assertEqual(_count_anchors(html), 1)
        self.assertIn('href="/blog/example"', html)

    def test_renders_canonical_clickable_article_class_string(self):
        html = _render_card()
        for token in (
            "group",
            "rounded-lg",
            "border",
            "border-border",
            "bg-card",
            "hover:border-accent/50",
        ):
            self.assertIn(token, html, f"missing article token {token!r}")
        # The a11y anchor contract from _clickable_card_classes.html is present.
        self.assertIn("focus-visible:ring-accent", html)
        self.assertIn('data-testid="unit-card"', html)

    def test_renders_the_single_top_right_arrow_by_default(self):
        html = _render_card()
        self.assertEqual(html.count(CANONICAL_ARROW), 1)
        self.assertIn(ARROW_MOBILE_RULE, html)
        self.assertIn("group-hover:translate-x-1", html)

    def test_card_arrow_false_removes_arrow_but_keeps_clickable_anchor(self):
        html = _render_card(card_arrow=False)
        self.assertEqual(_count_anchors(html), 1)
        self.assertNotIn(CANONICAL_ARROW, html)

    def test_surface_defaults_to_bg_card(self):
        html = _render_card()
        self.assertIn("bg-card", html)
        self.assertNotIn("bg-background", html)

    def test_surface_honours_bg_background_band_contrast(self):
        html = _render_card(card_surface_class="bg-background")
        # The article surface token flips; bg-card must not appear on the shell.
        article_open = html[: html.index(">", html.index("<article"))]
        self.assertIn("bg-background", article_open)
        self.assertNotIn("bg-card", article_open)

    def test_badge_and_media_templates_render_when_supplied(self):
        html = _render_card(
            card_badge_template="content/_home_workshop_card_badges.html",
            workshop=SimpleNamespace(pages_required_level=0),
        )
        # The workshop label badge lands in the header row cluster.
        self.assertIn("Workshop", html)


class InfoCardClassStringTest(SimpleTestCase):
    def test_outputs_static_class_string_only(self):
        rendered = render_to_string("content/_info_card_classes.html", {}).strip()
        self.assertEqual(rendered, "rounded-lg border border-border p-6")

    def test_has_no_group_hover_or_anchor(self):
        rendered = render_to_string("content/_info_card_classes.html", {}).strip()
        self.assertNotIn("group", rendered)
        self.assertNotIn("hover:", rendered)
        self.assertNotIn("<a", rendered)


class HomeInfoCardsAreStaticTest(TestCase):
    """Phase 1: home feature/activity/plan/sprint-story cards are info cards."""

    def test_static_info_cards_have_no_hover_or_arrow(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # The static-card sections must not promise navigation with an accent
        # hover border, and must not carry the clickable-card arrow.
        for marker in (
            'data-testid="home-activity-card"',
            'data-testid="home-personalized-plan-callout"',
            'data-testid="home-sprint-story-steps"',
        ):
            self.assertIn(marker, body)

        marker = body.index('data-testid="home-activity-card"')
        activity_card = body[marker - 200 : marker + 200]
        self.assertNotIn("hover:border-accent", activity_card)
        self.assertNotIn(CANONICAL_ARROW, activity_card)


class HomeFeaturedSprintSpotlightTest(TestCase):
    def test_featured_sprint_card_is_rounded_xl_spotlight(self):
        response = self.client.get("/")
        body = response.content.decode()
        card = body[
            body.index('data-testid="home-featured-sprint-card"') - 260 : body.index(
                'data-testid="home-featured-sprint-card"'
            )
            + 40
        ]
        self.assertIn("rounded-xl", card)
        self.assertIn("p-5", card)
        self.assertIn("sm:p-8", card)


class HomeBlogAndWorkshopCatalogCardsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title="Card System Article",
            slug="card-system-article",
            description="Body.",
            content_markdown="# Body",
            author="Author",
            published=True,
            date=datetime.date(2026, 1, 2),
        )
        cls.workshop = Workshop.objects.create(
            title="Card System Workshop",
            slug="card-system-workshop",
            status="published",
            date=datetime.date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )

    def test_blog_card_has_arrow_and_no_trailing_read_article_span(self):
        response = self.client.get("/")
        body = response.content.decode()
        self.assertIn('data-testid="home-blog-card"', body)
        self.assertNotIn("Read article", body)
        card = body[
            body.index('data-testid="home-blog-card"') : body.index(
                'data-testid="home-blog-card"'
            )
            + 1200
        ]
        self.assertIn(CANONICAL_ARROW, card)
        self.assertIn(ARROW_MOBILE_RULE, card)

    def test_workshop_card_passes_bg_background_and_has_arrow(self):
        response = self.client.get("/")
        body = response.content.decode()
        self.assertIn('data-testid="home-workshop-card"', body)
        card = body[
            body.index('data-testid="home-workshop-card"') - 120 : body.index(
                'data-testid="home-workshop-card"'
            )
            + 1400
        ]
        self.assertIn("bg-background", card)
        self.assertIn(CANONICAL_ARROW, card)
        self.assertNotIn("View workshop", card)


class TestimonialCardsAreFlatTest(TestCase):
    def test_testimonial_card_has_no_shadow(self):
        rendered = render_to_string(
            "includes/testimonial_cards.html",
            {
                "testimonials": [
                    {
                        "quote": "It shipped.",
                        "name": "Dana",
                        "role": "Engineer",
                        "company": "Acme",
                    }
                ]
            },
        )
        self.assertIn('data-testid="testimonial-card"', rendered)
        self.assertNotIn("shadow-sm", rendered)
