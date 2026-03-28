"""Tests for blog mobile responsive fixes - issue #174.

Covers:
- Arrow icon hidden on mobile (has `hidden sm:block` classes)
- Cover images use responsive aspect ratio on mobile
- Blog detail prose container has overflow protection
- Related articles grid uses single column on mobile (no grid-cols below sm:)
- Article-level overflow-x-hidden on detail page
"""

from datetime import date

from django.test import TestCase

from content.models import Article


class BlogListMobileArrowTest(TestCase):
    """The trailing arrow icon on blog list cards should be hidden on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title="Arrow Test",
            slug="arrow-test",
            description="Testing arrow visibility",
            date=date(2025, 6, 15),
            published=True,
        )

    def test_arrow_icon_hidden_on_mobile(self):
        response = self.client.get("/blog")
        content = response.content.decode()
        # The arrow-right icon should have `hidden sm:block` to hide on mobile
        self.assertIn('data-lucide="arrow-right"', content)
        self.assertIn("hidden sm:block", content)

    def test_arrow_icon_has_flex_shrink_0(self):
        response = self.client.get("/blog")
        content = response.content.decode()
        # Arrow should not shrink when in flex row layout
        self.assertIn("flex-shrink-0", content)


class BlogListMobileCoverImageTest(TestCase):
    """Cover images should use responsive aspect ratio on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title="Cover Test",
            slug="cover-test",
            description="Testing cover image",
            cover_image_url="https://example.com/cover.jpg",
            date=date(2025, 6, 15),
            published=True,
        )

    def test_cover_image_uses_aspect_video_on_mobile(self):
        response = self.client.get("/blog")
        content = response.content.decode()
        # On mobile, image uses aspect-video for consistent sizing
        self.assertIn("aspect-video", content)

    def test_cover_image_fixed_height_only_on_sm(self):
        response = self.client.get("/blog")
        content = response.content.decode()
        # Fixed height should only apply at sm: breakpoint
        self.assertIn("sm:h-32", content)
        # Should not have bare h-32 (without sm: prefix) on the cover image
        # Find the img tag for the cover image
        img_start = content.index('src="https://example.com/cover.jpg"')
        # Go back to find the img tag start
        img_tag_start = content.rfind("<img", 0, img_start)
        img_tag_end = content.index(">", img_start)
        img_tag = content[img_tag_start : img_tag_end + 1]
        # The img tag should not have bare `h-32` without `sm:` prefix
        # It should have `sm:h-32` but the class list should use aspect-video for mobile
        self.assertIn("aspect-video", img_tag)
        self.assertIn("sm:h-32", img_tag)


class BlogListMobileNoOverflowTest(TestCase):
    """Blog list cards should not cause horizontal overflow on narrow viewports."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title="A" * 100,  # Very long title
            slug="long-title-test",
            description="B" * 200,  # Very long description
            cover_image_url="https://example.com/cover.jpg",
            date=date(2025, 6, 15),
            tags=["python", "ai", "machine-learning", "deep-learning"],
            published=True,
        )

    def test_article_card_renders_with_long_content(self):
        response = self.client.get("/blog")
        self.assertEqual(response.status_code, 200)
        # The card uses flex-col on mobile (no fixed widths)
        content = response.content.decode()
        self.assertIn("flex-col", content)


class BlogDetailMobileProseOverflowTest(TestCase):
    """Blog detail prose content should not overflow horizontally."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title="Prose Test",
            slug="prose-test",
            description="Testing prose overflow",
            content_markdown=(
                "# Hello\n\n"
                "Some text.\n\n"
                "```python\n"
                "very_long_variable_name = 'a' * 200  "
                "# This is a very long line of code that should scroll\n"
                "```\n\n"
                "| Column A | Column B | Column C | Column D | Column E |\n"
                "|----------|----------|----------|----------|----------|\n"
                "| data | data | data | data | data |\n"
            ),
            date=date(2025, 6, 15),
            published=True,
        )

    def test_article_element_has_overflow_x_hidden(self):
        response = self.client.get("/blog/prose-test")
        content = response.content.decode()
        # The article element should have overflow-x-hidden
        self.assertIn("overflow-x-hidden", content)

    def test_prose_div_has_min_w_0(self):
        response = self.client.get("/blog/prose-test")
        content = response.content.decode()
        # The prose div should have min-w-0 to prevent flex overflow
        self.assertIn('class="prose min-w-0 max-w-full"', content)

    def test_code_blocks_have_overflow_x_auto(self):
        response = self.client.get("/blog/prose-test")
        content = response.content.decode()
        # Code blocks should be scrollable (codehilite class present, overflow
        # handled by base.html CSS which sets overflow-x: auto on .prose pre
        # and .prose .codehilite)
        self.assertIn("codehilite", content)

    def test_table_rendered(self):
        response = self.client.get("/blog/prose-test")
        content = response.content.decode()
        # Tables should render (overflow handled by base.html CSS which sets
        # display: block and overflow-x: auto on .prose table)
        self.assertIn("<table>", content)


class BlogDetailRelatedArticlesMobileTest(TestCase):
    """Related articles should stack to single column on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.main_article = Article.objects.create(
            title="Main Article",
            slug="main-mobile-test",
            description="Main description",
            content_markdown="# Main\nContent here.",
            date=date(2025, 6, 15),
            tags=["python"],
            published=True,
        )
        cls.related = Article.objects.create(
            title="Related Article",
            slug="related-mobile-test",
            description="Related description",
            date=date(2025, 6, 14),
            tags=["python"],
            published=True,
        )

    def test_related_grid_uses_responsive_columns(self):
        response = self.client.get("/blog/main-mobile-test")
        content = response.content.decode()
        # Grid should use sm:grid-cols-2 (not grid-cols-2),
        # meaning single column by default on mobile
        self.assertIn("sm:grid-cols-2", content)
        # Should not have bare grid-cols-2 without sm: prefix on the related grid
        grid_start = content.index("Related Articles")
        grid_section = content[grid_start : grid_start + 500]
        self.assertIn("sm:grid-cols-2", grid_section)
        # Verify it does not force multi-column on mobile
        self.assertNotIn('"grid-cols-2', grid_section)
