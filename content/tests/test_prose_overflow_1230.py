"""Template contracts for wide rendered prose containment (#1230)."""

import re
from datetime import date
from pathlib import Path

from django.test import TestCase, tag

from content.models import Project, Workshop, WorkshopPage

WIDE_TABLE = """| Topic | Foundation | Delivery | Operations |
| --- | --- | --- | --- |
| Python Fundamentals | Retrieval Augmented Generation | Evaluation Framework | Production Observability |
"""


def _classes_for_testid(html, testid):
    match = re.search(
        rf'<div class="([^"]*)" data-testid="{re.escape(testid)}">',
        html,
    )
    if match is None:
        raise AssertionError(f"Missing div[data-testid={testid!r}]")
    return match.group(1).split()


@tag("visual_regression")
class ProseOverflowTemplateContractTest(TestCase):
    """Protect the exact CSS and wrapper utilities owned by this issue."""

    @classmethod
    def setUpTestData(cls):
        cls.open_workshop = Workshop.objects.create(
            slug="open-overflow-1230",
            title="Open Overflow Workshop",
            status="published",
            date=date(2026, 7, 13),
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.open_page = WorkshopPage.objects.create(
            workshop=cls.open_workshop,
            slug="architecture",
            title="Architecture",
            sort_order=1,
            body=WIDE_TABLE,
        )
        cls.gated_workshop = Workshop.objects.create(
            slug="gated-overflow-1230",
            title="Gated Overflow Workshop",
            status="published",
            date=date(2026, 7, 13),
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=10,
        )
        cls.gated_page = WorkshopPage.objects.create(
            workshop=cls.gated_workshop,
            slug="comparison",
            title="Comparison",
            sort_order=1,
            body=WIDE_TABLE + "\n\n" + ("teaser context " * 160),
        )
        cls.open_project = Project.objects.create(
            title="Open Overflow Project",
            slug="open-overflow-project-1230",
            date=date(2026, 7, 13),
            content_markdown=WIDE_TABLE,
            required_level=0,
            published=True,
        )

    def test_global_prose_table_contract_keeps_cells_on_one_line(self):
        css = Path("templates/base.html").read_text()

        self.assertRegex(
            css,
            r"\.prose table \{[^}]*overflow-x: auto;[^}]*display: block;[^}]*\}",
        )
        self.assertRegex(css, r"\.prose th \{[^}]*white-space: nowrap;[^}]*\}")
        self.assertRegex(css, r"\.prose td \{[^}]*white-space: nowrap;[^}]*\}")
        self.assertRegex(css, r"\.prose pre \{[^}]*overflow-x: auto;[^}]*\}")
        self.assertRegex(
            css,
            r"\.prose \.codehilite \{[^}]*overflow-x: auto;[^}]*\}",
        )
        prose_rule = re.search(r"\.prose \{([^}]*)\}", css)
        self.assertIsNotNone(prose_rule)
        self.assertIn("word-break: break-word", prose_rule.group(1))

    def test_accessible_workshop_page_body_has_local_overflow_guard(self):
        response = self.client.get(self.open_page.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        classes = _classes_for_testid(response.content.decode(), "page-body")
        self.assertIn("prose-tight", classes)
        self.assertIn("mb-12", classes)
        self.assertIn("max-w-full", classes)
        self.assertIn("overflow-x-auto", classes)

    def test_gated_workshop_teaser_has_guard_and_preserves_gate(self):
        response = self.client.get(self.gated_page.get_absolute_url())
        html = response.content.decode()

        self.assertEqual(response.status_code, 403)
        classes = _classes_for_testid(html, "teaser-body")
        self.assertIn("teaser-body", classes)
        self.assertIn("max-w-full", classes)
        self.assertIn("overflow-x-auto", classes)
        self.assertIn('data-testid="teaser-body-wrapper"', html)
        self.assertIn("bg-gradient-to-b", html)
        self.assertIn('data-testid="page-upgrade-cta"', html)
        self.assertNotIn('data-testid="page-body"', html)

    def test_accessible_project_body_has_local_overflow_guard(self):
        response = self.client.get(self.open_project.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        classes = _classes_for_testid(response.content.decode(), "project-body")
        self.assertIn("prose", classes)
        self.assertIn("max-w-full", classes)
        self.assertIn("overflow-x-auto", classes)


@tag("core")
class GatedProjectOverflowSafetyTest(TestCase):
    """The new accessible wrapper must never expose a gated project body."""

    def test_gated_project_omits_body_and_overflow_wrapper(self):
        project = Project.objects.create(
            title="Protected Overflow Project",
            slug="protected-overflow-project-1230",
            description="A protected project whose full body stays gated.",
            date=date(2026, 7, 13),
            content_markdown=WIDE_TABLE + "\n\nPROTECTED_OVERFLOW_MARKER",
            required_level=10,
            published=True,
        )

        response = self.client.get(project.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="project-paywall"')
        self.assertNotContains(response, 'data-testid="project-body"')
        self.assertNotContains(response, "PROTECTED_OVERFLOW_MARKER")
