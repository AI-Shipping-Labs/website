"""Static and rendered regressions for issue #1281 public cover polish."""

import datetime
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from content.models import Project


class PublicPolishTemplateContractTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = Path(settings.BASE_DIR)

    def _read(self, relative_path):
        return (self.root / relative_path).read_text(encoding="utf-8")

    def test_pricing_stretches_only_at_desktop_and_keeps_carousel_contract(self):
        template = self._read("templates/payments/pricing.html")
        marker = template.index('data-testid="pricing-tier-carousel"')
        grid_start = template.rfind("<div ", 0, marker)
        grid = template[grid_start:marker]

        for token in (
            "items-start",
            "lg:items-stretch",
            "lg:grid-cols-4",
            "max-lg:flex",
            "max-lg:snap-x",
            "max-lg:snap-mandatory",
            "max-lg:overflow-x-auto",
            "max-lg:pt-6",
        ):
            self.assertIn(token, grid)
        self.assertIn("flex flex-col rounded-xl", template)
        self.assertIn('class="mb-8 flex-1', template)
        self.assertIn('<div class="mt-auto">', template)

    def test_page_and_detail_heading_recipes_remain_distinct(self):
        design_system = self._read("_docs/design-system.md")
        self.assertIn(
            "| Page h1 | `text-3xl font-semibold tracking-tight sm:text-4xl` |",
            design_system,
        )
        self.assertIn(
            "| Detail hero h1 | `text-3xl font-semibold tracking-tight "
            "sm:text-4xl lg:text-5xl` |",
            design_system,
        )

        workshops = self._read("templates/content/workshops_list.html")
        self.assertIn("text-3xl font-semibold tracking-tight", workshops)
        self.assertNotIn("lg:text-5xl", workshops)
        for detail in (
            "templates/content/blog_detail.html",
            "templates/content/course_detail.html",
            "templates/content/marketing_page.html",
            "templates/content/project_detail.html",
            "templates/content/tutorial_detail.html",
        ):
            self.assertIn(
                "text-3xl font-semibold tracking-tight sm:text-4xl lg:text-5xl",
                self._read(detail),
                detail,
            )

    def test_shared_preview_and_project_detail_error_contracts(self):
        preview = self._read("templates/content/_content_preview.html")
        self.assertEqual(preview.count("<img"), 1)
        self.assertIn('loading="{{ preview_image_loading|default:\'lazy\' }}"', preview)
        self.assertIn('decoding="async"', preview)
        self.assertIn(
            "onerror=\"this.hidden = true; this.nextElementSibling.hidden = false; "
            "this.nextElementSibling.classList.remove('hidden');\"",
            preview,
        )
        self.assertIn(
            'class="{% if preview_cover_url %}hidden {% endif %}flex',
            preview,
        )
        self.assertIn(
            'data-testid="{{ preview_testid }}-fallback"{% if preview_cover_url %} hidden{% endif %}',
            preview,
        )

        detail = self._read("templates/content/project_detail.html")
        self.assertIn('data-testid="project-detail-cover"', detail)
        self.assertIn('data-testid="project-detail-cover-image"', detail)
        self.assertIn(
            "onerror=\"this.closest('[data-testid=project-detail-cover]').hidden = true;\"",
            detail,
        )

    def test_stale_controls_keep_accessible_existing_contracts(self):
        dashboard = self._read("templates/content/dashboard.html")
        slack = self._read("templates/includes/_slack_account_card.html")
        account = self._read("templates/accounts/account.html")

        self.assertIn('id="dismiss-success-banner"', dashboard)
        self.assertGreaterEqual(dashboard.count("h-11 w-11"), 2)
        self.assertGreaterEqual(dashboard.count('aria-label="Dismiss"'), 2)
        self.assertIn('data-testid="onboarding-prompt-dismiss"', dashboard)
        self.assertIn("focus-visible:ring-2", dashboard)
        self.assertIn("relative h-full", slack)
        self.assertIn("absolute right-3 top-3", slack)
        self.assertIn("h-11 w-11", slack)
        self.assertIn('aria-label="Dismiss"', slack)
        self.assertIn('data-testid="slack-account-card-dismiss"', slack)
        self.assertIn("pr-12 sm:pr-0", slack)
        self.assertIn("extra='shrink-0 whitespace-nowrap'", account)
        self.assertIn('data-testid="member-api-key-create-submit"', account)


class ProjectDetailCoverContractTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        common = {
            "description": "Cover fallback detail fixture.",
            "content_markdown": "Visible body.",
            "date": datetime.date(2026, 7, 17),
            "published": True,
        }
        cls.covered = Project.objects.create(
            title="Covered project 1281",
            slug="covered-project-1281",
            cover_image_url="https://cdn.example.com/project-1281.png",
            **common,
        )
        cls.coverless = Project.objects.create(
            title="Coverless project 1281",
            slug="coverless-project-1281",
            **common,
        )

    def test_covered_detail_emits_stable_runtime_error_contract(self):
        response = self.client.get(self.covered.get_absolute_url())

        self.assertContains(response, 'data-testid="project-detail-cover"', count=1)
        self.assertContains(response, 'data-testid="project-detail-cover-image"', count=1)
        self.assertContains(response, 'src="https://cdn.example.com/project-1281.png"')
        self.assertContains(response, 'alt="Covered project 1281"')
        self.assertContains(
            response,
            "onerror=\"this.closest('[data-testid=project-detail-cover]').hidden = true;\"",
        )

    def test_coverless_detail_emits_no_optional_media_slot(self):
        response = self.client.get(self.coverless.get_absolute_url())

        self.assertNotContains(response, 'data-testid="project-detail-cover"')
        self.assertNotContains(response, 'data-testid="project-detail-cover-image"')
        self.assertContains(response, "Coverless project 1281")
        self.assertContains(response, 'data-testid="project-body"')
