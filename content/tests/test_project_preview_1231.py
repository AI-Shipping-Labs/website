"""Project preview and detail badge regressions for issue #1231."""

import datetime
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, tag

from content.models import Project


class ProjectDisplayImageUrlTest(SimpleTestCase):
    def test_display_image_url_uses_exact_banner_precedence(self):
        project = Project(
            cover_image_url="https://cdn.example.com/project-cover.png",
            custom_banner_url="https://cdn.example.com/project-custom.png",
            auto_banner_url="https://cdn.example.com/project-auto.png",
        )

        cases = (
            ("https://cdn.example.com/project-cover.png",),
            ("", "https://cdn.example.com/project-custom.png"),
            ("", "", "https://cdn.example.com/project-auto.png"),
            ("", "", ""),
        )
        expected_urls = (
            "https://cdn.example.com/project-cover.png",
            "https://cdn.example.com/project-custom.png",
            "https://cdn.example.com/project-auto.png",
            "",
        )

        for values, expected_url in zip(cases, expected_urls, strict=True):
            with self.subTest(expected_url=expected_url or "empty"):
                padded = values + ("",) * (3 - len(values))
                (
                    project.cover_image_url,
                    project.custom_banner_url,
                    project.auto_banner_url,
                ) = padded
                self.assertEqual(project.display_image_url, expected_url)

    def test_display_image_url_delegates_to_shared_resolver(self):
        project = Project()
        with patch(
            "content.models.project.effective_banner_url",
            return_value="https://cdn.example.com/resolved.png",
        ) as resolver:
            self.assertEqual(
                project.display_image_url,
                "https://cdn.example.com/resolved.png",
            )

        resolver.assert_called_once_with(project)


class SharedProjectPreviewRenderingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        common = {
            "description": "Preview fixture description.",
            "author": "AI Shipping Labs",
            "difficulty": "intermediate",
            "reading_time": "5 min read",
            "estimated_time": "2 hours",
            "tags": ["agents", "python"],
            "published": True,
        }
        cls.cover_project = Project.objects.create(
            title="Cover Preview Project 1231",
            slug="cover-preview-project-1231",
            date=datetime.date(2026, 7, 10),
            cover_image_url="https://cdn.example.com/1231-cover.png",
            custom_banner_url="https://cdn.example.com/1231-cover-custom.png",
            auto_banner_url="https://cdn.example.com/1231-cover-auto.png",
            required_level=10,
            **common,
        )
        cls.custom_project = Project.objects.create(
            title="Custom Preview Project 1231",
            slug="custom-preview-project-1231",
            date=datetime.date(2026, 7, 11),
            custom_banner_url="https://cdn.example.com/1231-custom.png",
            auto_banner_url="https://cdn.example.com/1231-custom-auto.png",
            **common,
        )
        cls.auto_project = Project.objects.create(
            title="Auto Preview Project 1231",
            slug="auto-preview-project-1231",
            date=datetime.date(2026, 7, 12),
            auto_banner_url="https://cdn.example.com/1231-auto.png",
            **common,
        )
        cls.fallback_project = Project.objects.create(
            title="Fallback Preview Project 1231",
            slug="fallback-preview-project-1231",
            date=datetime.date(2026, 7, 13),
            **common,
        )

    def test_listing_renders_one_shared_preview_per_card(self):
        response = self.client.get("/projects")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-testid="project-card-preview"',
            count=4,
        )
        self.assertContains(
            response,
            'data-testid="project-card-preview-image"',
            count=3,
        )
        self.assertContains(
            response,
            'data-testid="project-card-preview-fallback"',
            count=4,
        )
        self.assertContains(
            response,
            'data-testid="project-card-preview-fallback" hidden',
            count=3,
        )
        self.assertContains(response, "https://cdn.example.com/1231-cover.png")
        self.assertNotContains(
            response,
            "https://cdn.example.com/1231-cover-custom.png",
        )
        self.assertNotContains(
            response,
            "https://cdn.example.com/1231-cover-auto.png",
        )
        self.assertContains(response, "https://cdn.example.com/1231-custom.png")
        self.assertNotContains(
            response,
            "https://cdn.example.com/1231-custom-auto.png",
        )
        self.assertContains(response, "https://cdn.example.com/1231-auto.png")
        self.assertContains(
            response,
            'data-lucide="rocket"',
        )

    def test_shared_card_preserves_content_badges_and_canonical_links(self):
        for path, card_testid in (
            ("/", "home-project-card"),
            ("/projects", "project-card"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'data-testid="{card_testid}"')
                self.assertContains(response, self.fallback_project.title)
                self.assertContains(
                    response,
                    f'href="{self.fallback_project.get_absolute_url()}"',
                )
                self.assertContains(response, "Preview fixture description.")
                self.assertContains(response, "AI Shipping Labs")
                self.assertContains(response, "intermediate")
                self.assertContains(response, "Official")
                self.assertContains(response, "5 min read")
                self.assertContains(response, "2 hours")
                self.assertContains(response, "agents")
                self.assertContains(response, 'data-lucide="arrow-right"')

    def test_homepage_uses_same_resolved_preview_and_destination(self):
        home = self.client.get("/")
        listing = self.client.get("/projects")
        expected_url = self.custom_project.display_image_url
        expected_href = self.custom_project.get_absolute_url()

        for response in (home, listing):
            with self.subTest(template=response.templates[0].name):
                self.assertContains(response, expected_url)
                self.assertContains(response, f'href="{expected_href}"')


class ProjectDetailBadgeAndGridTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title="Detail Badge Project 1231",
            slug="detail-badge-project-1231",
            description="Detail badge fixture.",
            content_markdown="Project body.",
            date=datetime.date(2026, 7, 13),
            author="Builder",
            difficulty="beginner",
            published=True,
        )

    def test_detail_type_uses_shared_badge_before_title(self):
        response = self.client.get(self.project.get_absolute_url())
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-component="member-badge"')
        self.assertContains(response, 'data-lucide="rocket"')
        self.assertContains(response, "Project")
        self.assertLess(
            html.index('data-component="member-badge"'),
            html.index('<h1 class="text-3xl'),
        )
        self.assertContains(response, "by Builder")
        self.assertContains(response, "Detail badge fixture.")
        self.assertContains(response, "beginner")
        self.assertContains(response, 'data-testid="project-body"')

    @tag("visual_regression")
    def test_detail_badge_uses_requested_tone_and_size(self):
        response = self.client.get(self.project.get_absolute_url())

        self.assertContains(
            response,
            "gap-2 px-4 py-1.5 text-sm border border-accent/30 "
            "bg-accent/10 text-accent",
        )
        self.assertContains(response, 'data-lucide="rocket" class="h-4 w-4"')

    @tag("visual_regression")
    def test_projects_grid_uses_default_gap_and_preserves_breakpoints(self):
        response = self.client.get("/projects")

        self.assertContains(
            response,
            'class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3"',
        )
        self.assertNotContains(response, 'class="grid gap-5 sm:grid-cols-2')
