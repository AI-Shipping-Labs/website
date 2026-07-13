"""Focused contracts for the public-page design-system corrections in #1229."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

TEMPLATES = Path(settings.BASE_DIR) / "templates"


def _source(relative_path):
    return (TEMPLATES / relative_path).read_text(encoding="utf-8")


def _class_tokens_for_text(html, tag_name, text):
    match = re.search(
        rf'<{tag_name}\b[^>]*class="([^"]*)"[^>]*>\s*{re.escape(text)}',
        html,
    )
    if match is None:
        raise AssertionError(f"Could not find <{tag_name}> containing {text!r}")
    return set(match.group(1).split())


@tag("visual_regression")
class StaticPageTypographyContractTest(TestCase):
    """Tailwind typography tokens stay aligned with the public-page scale."""

    def test_about_headings_use_semibold_tight_tokens(self):
        html = self.client.get("/about").content.decode()
        expected = {
            ("h1", "About AI Shipping Labs"): {
                "text-3xl", "font-semibold", "tracking-tight", "sm:text-4xl",
            },
            ("h2", "Founders"): {
                "text-2xl", "font-semibold", "tracking-tight",
            },
            ("h3", "Alexey Grigorev"): {
                "text-2xl", "font-semibold", "tracking-tight",
            },
            ("h3", "Valeriia Kuka"): {
                "text-2xl", "font-semibold", "tracking-tight",
            },
        }
        for (tag_name, text), required in expected.items():
            with self.subTest(text=text):
                tokens = _class_tokens_for_text(html, tag_name, text)
                self.assertTrue(required <= tokens)
                self.assertNotIn("font-bold", tokens)

    def test_faq_and_legal_h1s_use_page_heading_tokens(self):
        expected = {
            "/faq": "Frequently Asked Questions",
            "/terms": "Terms of Service",
            "/privacy": "Privacy Policy",
            "/impressum": "Impressum",
        }
        required = {
            "text-3xl", "font-semibold", "tracking-tight", "sm:text-4xl",
        }
        for path, text in expected.items():
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                tokens = _class_tokens_for_text(html, "h1", text)
                self.assertTrue(required <= tokens)
                self.assertNotIn("font-bold", tokens)

    def test_every_legal_text_2xl_h2_is_semibold_and_tight(self):
        expected_counts = {
            "legal/terms.html": 14,
            "legal/privacy.html": 10,
            "legal/impressum.html": 9,
        }
        for template_name, expected_count in expected_counts.items():
            with self.subTest(template_name=template_name):
                source = _source(template_name)
                classes = re.findall(
                    r'<h2 class="([^"]*\btext-2xl\b[^"]*)"', source,
                )
                self.assertEqual(len(classes), expected_count)
                for class_string in classes:
                    tokens = set(class_string.split())
                    self.assertIn("font-semibold", tokens)
                    self.assertIn("tracking-tight", tokens)

    def test_home_testimonial_heading_matches_marketing_hierarchy(self):
        html = self.client.get("/").content.decode()
        eyebrow = _class_tokens_for_text(html, "p", "What learners say")
        heading = _class_tokens_for_text(
            html,
            "h2",
            "From the students of our AI Engineering course",
        )
        self.assertIn("tracking-widest", eyebrow)
        self.assertNotIn("tracking-wider", eyebrow)
        self.assertTrue(
            {"text-3xl", "sm:text-4xl", "font-semibold", "tracking-tight"}
            <= heading
        )


@tag("visual_regression")
class StaticPageSpacingAndControlContractTest(TestCase):
    """Exact scoped rhythm and anonymous footer control sizes stay canonical."""

    def test_home_faq_partial_uses_marketing_section_rhythm(self):
        source = _source("includes/_faq_section.html")
        self.assertIn("py-12 sm:py-20 lg:py-28", source)
        self.assertNotIn("py-16 sm:py-24 lg:py-32", source)

    def test_anonymous_footer_controls_use_documented_sizes(self):
        html = self.client.get("/").content.decode()
        input_match = re.search(
            r'<input type="email" name="email" required\s+'
            r'placeholder="Enter your email"\s+class="([^"]*)"',
            html,
        )
        button_match = re.search(
            r'<button type="submit"\s+class="([^"]*)">\s*Subscribe',
            html,
        )
        self.assertIsNotNone(input_match)
        self.assertIsNotNone(button_match)
        input_tokens = set(input_match.group(1).split())
        button_tokens = set(button_match.group(1).split())
        self.assertTrue({"px-4", "py-3", "text-sm"} <= input_tokens)
        self.assertTrue({"px-6", "py-3", "text-base"} <= button_tokens)
        self.assertNotIn("py-2.5", input_tokens)
        self.assertFalse({"px-5", "py-2.5"} & button_tokens)


class StaticPageBehaviorPreservationTest(TestCase):
    """Presentation-only changes retain callers, semantics, and suppression."""

    def test_shared_faq_partial_has_home_as_its_only_page_caller(self):
        home = _source("home.html")
        about = _source("content/about.html")
        standalone_faq = _source("content/faq.html")
        partial = _source("includes/_faq_section.html")
        include = '{% include "includes/_faq_section.html" %}'
        self.assertEqual(home.count(include), 1)
        self.assertNotIn(include, about)
        self.assertNotIn(include, standalone_faq)
        self.assertIn("Used on the marketing homepage (templates/home.html).", partial)
        self.assertNotIn("About page", partial)

    def test_home_and_standalone_faq_keep_disclosure_behavior_and_boundary(self):
        home = self.client.get("/")
        faq = self.client.get("/faq")
        about = self.client.get("/about")
        self.assertTemplateUsed(home, "includes/_faq_section.html")
        self.assertTemplateUsed(home, "includes/_accordion.html")
        self.assertTemplateUsed(faq, "includes/_accordion.html")
        self.assertTemplateNotUsed(faq, "includes/_faq_section.html")
        self.assertTemplateNotUsed(about, "includes/_faq_section.html")
        self.assertIn('id="faq"', home.content.decode())
        self.assertNotIn('id="faq"', faq.content.decode())

    def test_footer_form_keeps_native_semantics_and_feedback_hooks(self):
        html = self.client.get("/").content.decode()
        self.assertIn(
            '<form class="subscribe-form flex flex-col gap-3 sm:flex-row '
            'sm:items-center sm:justify-center" novalidate '
            'onsubmit="return handleFooterSubscribe(event, this)">',
            html,
        )
        self.assertRegex(html, r'<input type="email" name="email" required')
        self.assertRegex(html, r'<button type="submit"[^>]*>\s*Subscribe')
        self.assertIn("footer-subscribe-message", html)
        self.assertIn("footer-subscribe-error", html)

    def test_footer_newsletter_remains_hidden_for_authenticated_users(self):
        user = get_user_model().objects.create_user(
            email="static-design-1229@test.com",
            password="TestPass123!",
        )
        self.client.force_login(user)
        html = self.client.get("/about").content.decode()
        self.assertNotIn('<div class="subscribe-form-container"', html)
        self.assertNotIn('id="newsletter"', html)

    def test_footer_newsletter_remains_hidden_on_suppressed_surface(self):
        html = self.client.get("/pricing").content.decode()
        self.assertNotIn('<div class="subscribe-form-container"', html)
        self.assertNotIn('id="newsletter"', html)
