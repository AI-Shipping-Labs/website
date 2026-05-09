"""Tests for the standalone FAQ page and FAQ rendering.

Issue #558 extracts FAQ from the About page into a standalone /faq page.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape as _html_escape

from content.views.home import FAQ_ITEMS


class FaqUrlResolutionTest(TestCase):
    """`reverse('faq')` resolves to `/faq`."""

    def test_faq_url_reverses_to_faq_path(self):
        self.assertEqual(reverse("faq"), "/faq")


class FaqStandalonePageTest(TestCase):
    """`/faq` returns HTTP 200 with the standalone FAQ page."""

    def test_anonymous_gets_200(self):
        response = self.client.get("/faq")
        self.assertEqual(response.status_code, 200)

    def test_authenticated_gets_200(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            email="faq-user@test.com",
            password="TestPass123!",
        )
        self.client.force_login(user)
        response = self.client.get("/faq")
        self.assertEqual(response.status_code, 200)

    def test_renders_faq_template(self):
        response = self.client.get("/faq")
        self.assertTemplateUsed(response, "content/faq.html")
        self.assertTemplateUsed(response, "includes/_accordion.html")

    def test_renders_every_faq_question(self):
        response = self.client.get("/faq")
        content = response.content.decode()
        for item in FAQ_ITEMS:
            self.assertIn(_html_escape(item["question"]), content)

    def test_page_title(self):
        response = self.client.get("/faq")
        self.assertContains(response, "<title>FAQ | AI Shipping Labs</title>")

    def test_meta_description(self):
        response = self.client.get("/faq")
        self.assertContains(
            response,
            '<meta name="description" content="Frequently asked questions about AI Shipping Labs membership, billing, tiers, and how to get started.">',
        )

    def test_back_to_home_link(self):
        response = self.client.get("/faq")
        self.assertContains(response, 'href="/"')
        self.assertContains(response, "Back to home")

    def test_page_heading(self):
        response = self.client.get("/faq")
        self.assertContains(
            response,
            "<h1",
        )
        self.assertContains(response, "Frequently Asked Questions")


class AboutPageNoFaqTest(TestCase):
    """The About page no longer includes the FAQ section."""

    def test_about_does_not_include_faq_partial(self):
        response = self.client.get("/about")
        self.assertTemplateNotUsed(response, "includes/_faq_section.html")

    def test_about_has_no_faq_anchor(self):
        response = self.client.get("/about")
        content = response.content.decode()
        self.assertNotIn('id="faq"', content)

    def test_about_meta_description_no_faq_mention(self):
        response = self.client.get("/about")
        content = response.content.decode()
        match = None
        import re
        match = re.search(
            r'<meta name="description" content="([^"]*)">', content,
        )
        self.assertIsNotNone(match)
        self.assertNotIn("frequently asked questions", match.group(1).lower())

    def test_about_title_unchanged(self):
        response = self.client.get("/about")
        self.assertContains(response, "<title>About | AI Shipping Labs</title>")


class HomepageFaqUnaffectedTest(TestCase):
    """The homepage FAQ section is unaffected by the extraction."""

    def test_homepage_has_faq_section_with_id(self):
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('id="faq"', content)

    def test_homepage_renders_faq_questions(self):
        response = self.client.get("/")
        content = response.content.decode()
        for item in FAQ_ITEMS:
            self.assertIn(_html_escape(item["question"]), content)
