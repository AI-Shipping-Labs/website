"""Focused guest-page layout polish regressions for issue #1190."""

import re
from datetime import date

from django.test import TestCase

from content.models import Article, Course, CuratedLink, Workshop


class GuestLayoutPolish1190Test(TestCase):
    def test_blog_cards_always_render_thumbnail_slot_with_fallback(self):
        Article.objects.create(
            title="Covered article",
            slug="covered-1190",
            description="Has a cover.",
            date=date(2026, 7, 1),
            cover_image_url="https://example.com/cover.png",
            tags=["agents"],
            published=True,
        )
        Article.objects.create(
            title="Coverless article",
            slug="coverless-1190",
            description="No cover.",
            date=date(2026, 7, 2),
            tags=["rag"],
            published=True,
        )

        response = self.client.get("/blog")
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('data-testid="blog-card-thumbnail"'), 2)
        self.assertEqual(
            html.count('data-testid="blog-card-thumbnail-fallback"'), 1,
        )
        self.assertContains(response, 'href="/blog?tag=rag"')

    def test_workshop_description_gets_local_overflow_guard(self):
        workshop = Workshop.objects.create(
            slug="overflow-1190",
            title="Overflow Workshop 1190",
            status="published",
            date=date(2026, 7, 2),
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            description=(
                "```mermaid\n"
                "flowchart LR\n"
                "FAQ[FAQ knowledge base] --> Q[generate synthetic questions]\n"
                "Q --> R[rank retrieved passages] --> E[evaluate answers]\n"
                "```\n"
            ),
        )

        response = self.client.get(workshop.get_absolute_url())
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<div class="([^"]*)" data-testid="workshop-description">',
            html,
        )
        self.assertIsNotNone(match)
        self.assertIn("max-w-full", match.group(1))
        self.assertIn("overflow-x-auto", match.group(1))
        self.assertIn('class="mermaid"', html)

    def test_resources_cards_omit_redundant_category_pill_and_empty_description(self):
        CuratedLink.objects.create(
            item_id="course-link-1190",
            title="Compact course link",
            description="",
            url="https://example.com/course-link",
            category="courses",
            tags=["llm"],
            source="Example",
            published=True,
        )
        CuratedLink.objects.create(
            item_id="course-link-desc-1190",
            title="Described course link",
            description="A useful course reference.",
            url="https://example.com/course-link-desc",
            category="courses",
            source="Example",
            published=True,
        )

        response = self.client.get("/resources")
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compact course link")
        self.assertNotIn('data-lucide="book-open" class="h-3.5 w-3.5"', html)
        self.assertNotIn(
            '<p class="mt-2 line-clamp-3 text-sm text-muted-foreground"></p>',
            html,
        )
        self.assertIn("grid items-start gap-6", html)
        self.assertIn("self-start overflow-hidden", html)

    def test_courses_low_count_grid_is_centered_and_capped(self):
        Course.objects.create(
            title="Small Catalog One",
            slug="small-catalog-one-1190",
            status="published",
            tags=["small"],
        )
        Course.objects.create(
            title="Small Catalog Two",
            slug="small-catalog-two-1190",
            status="published",
            tags=["small"],
        )
        Course.objects.create(
            title="Other Catalog Course",
            slug="other-catalog-course-1190",
            status="published",
            tags=["other"],
        )

        response = self.client.get("/courses?tag=small")
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<div class="([^"]*)" data-testid="courses-grid">',
            html,
        )
        self.assertIsNotNone(match)
        classes = match.group(1)
        self.assertIn("sm:grid-cols-2", classes)
        self.assertIn("lg:max-w-4xl", classes)
        self.assertNotIn("lg:grid-cols-3", classes)

    def test_courses_three_plus_keep_existing_three_column_grid(self):
        for index in range(3):
            Course.objects.create(
                title=f"Full Catalog {index}",
                slug=f"full-catalog-{index}-1190",
                status="published",
            )

        response = self.client.get("/courses")
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3" '
            'data-testid="courses-grid"',
            html,
        )

    def test_about_linkedin_links_keep_tap_target_and_focus_ring(self):
        response = self.client.get("/about")
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        for href in (
            "https://linkedin.com/in/agrigorev",
            "https://linkedin.com/in/valeriia-kuka",
        ):
            match = re.search(
                rf'<a href="{re.escape(href)}"[^>]*class="([^"]*)"',
                html,
            )
            self.assertIsNotNone(match)
            classes = match.group(1)
            self.assertIn("h-11", classes)
            self.assertIn("w-11", classes)
            self.assertIn("items-center", classes)
            self.assertIn("justify-center", classes)
            self.assertIn("focus-visible:ring-2", classes)
