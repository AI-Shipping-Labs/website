"""Public page CTA hierarchy guards for issue #403."""

import re

from django.test import TestCase

from content.models import CuratedLink
from tests.fixtures import TierSetupMixin


def _main_html(body):
    match = re.search(r"<main[\s\S]*?</main>", body)
    assert match, "No <main> element found"
    return match.group(0)


class PublicCtaHierarchyTest(TierSetupMixin, TestCase):
    def test_home_consolidates_newsletter_to_footer_anchor(self):
        response = self.client.get("/")
        body = response.content.decode()

        self.assertEqual(len(re.findall(r'<form[^>]*class="subscribe-form', body)), 1)
        self.assertIn('id="newsletter"', body)
        self.assertNotIn("Stop shipping alone.", body)
        self.assertIn('href="/#tiers"', body)
        self.assertIn('href="/resources"', body)

    def test_pricing_has_no_newsletter_cta_before_tier_grid(self):
        response = self.client.get("/pricing")
        body = response.content.decode()

        grid_start = body.index("<!-- Tier Grid")
        before_grid = body[:grid_start]
        self.assertNotIn('href="/#newsletter"', before_grid)
        self.assertNotIn('class="subscribe-form', before_grid)

    def test_resources_gated_cta_is_secondary(self):
        CuratedLink.objects.create(
            item_id="issue-403-gated-resource",
            title="Gated Resource",
            description="A gated resource for CTA hierarchy coverage.",
            url="https://example.com/resource",
            category="tools",
            required_level=1,
            published=True,
        )

        response = self.client.get("/resources")
        body = response.content.decode()
        gated_cta = body[body.index('class="gated-cta'):]
        match = re.search(r'<a href="/pricing" class="([^"]*)"', gated_cta)

        self.assertIsNotNone(match)
        classes = match.group(1)
        self.assertIn("bg-secondary", classes)
        self.assertNotIn("bg-accent", classes)

    def test_course_and_workshop_lists_keep_newsletter_out_of_main_content(self):
        for path in ("/courses", "/workshops"):
            response = self.client.get(path)
            main = _main_html(response.content.decode())
            self.assertNotIn('class="subscribe-form', main)
            self.assertNotIn('href="/#newsletter"', main)

    def test_blog_empty_state_is_browse_first(self):
        response = self.client.get("/blog")
        main = _main_html(response.content.decode())

        self.assertIn("Browse all articles as the archive grows.", main)
        self.assertNotIn('href="/#newsletter"', main)
