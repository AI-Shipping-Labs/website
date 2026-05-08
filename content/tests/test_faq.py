"""Tests for FAQ rendering and the legacy FAQ URL.

Issue #540 moves public FAQ content onto `/about#faq` while keeping
`reverse("faq")` and `/faq` as a permanent compatibility redirect.
"""

import re

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape as _html_escape

from content.views.home import FAQ_ITEMS


class FaqUrlResolutionTest(TestCase):
    """`reverse('faq')` resolves to `/faq`."""

    def test_faq_url_reverses_to_faq_path(self):
        self.assertEqual(reverse("faq"), "/faq")


class FaqLegacyRedirectTest(TestCase):
    """The legacy FAQ URL permanently redirects to the About FAQ anchor."""

    def test_anonymous_redirects_to_about_faq(self):
        response = self.client.get("/faq")
        self.assertRedirects(
            response,
            "/about#faq",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_authenticated_redirects_to_about_faq(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            email="faq-user@test.com",
            password="TestPass123!",
        )
        self.client.force_login(user)
        response = self.client.get("/faq")
        self.assertRedirects(
            response,
            "/about#faq",
            status_code=301,
            fetch_redirect_response=False,
        )


class AboutFaqTest(TestCase):
    """Anonymous and authenticated users can read FAQ content on `/about`."""

    def test_about_renders_every_faq_question(self):
        response = self.client.get("/about")
        content = response.content.decode()
        for item in FAQ_ITEMS:
            self.assertIn(_html_escape(item["question"]), content)

    def test_about_includes_shared_faq_partial(self):
        response = self.client.get("/about")
        self.assertTemplateUsed(response, "includes/_faq_section.html")
        self.assertTemplateUsed(response, "includes/_accordion.html")

    def test_about_has_faq_anchor(self):
        response = self.client.get("/about")
        content = response.content.decode()
        match = re.search(r'<section id="faq"', content)
        self.assertIsNotNone(
            match, 'About FAQ should render <section id="faq">'
        )


class HomepageFaqAnchorStillWorksTest(TestCase):
    """The `#faq` anchor on the homepage must still exist so the
    anon-flow CTA keeps scrolling to the right spot."""

    def test_homepage_has_faq_section_with_id(self):
        response = self.client.get("/")
        content = response.content.decode()
        # The partial wraps the section with id="faq" by default.
        self.assertIn('id="faq"', content)

    def test_homepage_renders_faq_questions(self):
        response = self.client.get("/")
        content = response.content.decode()
        for item in FAQ_ITEMS:
            self.assertIn(_html_escape(item["question"]), content)


class FaqPartialAcceptsCustomSectionId(TestCase):
    """Including the partial without overriding `section_id` defaults to
    `id="faq"` (the marketing-homepage anchor target)."""

    def test_default_section_id(self):
        response = self.client.get("/about")
        content = response.content.decode()
        match = re.search(r'<section id="faq"', content)
        self.assertIsNotNone(
            match, "FAQ partial should render <section id=\"faq\">"
        )
