"""Tests for the standalone FAQ page and the FAQ partial.

Issue #238: logged-in users were sent to `/#faq` (an anchor that only exists
on the marketing homepage) when clicking footer FAQ. The fix adds a real
`/faq` page that renders the same FAQ items in a standalone layout, while
the `#faq` anchor on the homepage remains for the anonymous-flow CTA.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape as _html_escape

from content.views.home import FAQ_ITEMS

User = get_user_model()


class FaqUrlResolutionTest(TestCase):
    """`reverse('faq')` resolves to `/faq`."""

    def test_faq_url_reverses_to_faq_path(self):
        self.assertEqual(reverse("faq"), "/faq")


class FaqPageAnonymousTest(TestCase):
    """Anonymous user can load `/faq` and see all questions."""

    def test_returns_200(self):
        response = self.client.get("/faq")
        self.assertEqual(response.status_code, 200)

    def test_uses_standalone_template(self):
        response = self.client.get("/faq")
        self.assertTemplateUsed(response, "content/faq.html")

    def test_includes_shared_faq_partial(self):
        response = self.client.get("/faq")
        self.assertTemplateUsed(response, "includes/_faq_section.html")

    def test_renders_every_faq_question(self):
        response = self.client.get("/faq")
        content = response.content.decode()
        for item in FAQ_ITEMS:
            # Apostrophes ("What's included...") are HTML-escaped to &#x27;
            # by Django's default autoescape, so escape the needle as well.
            self.assertIn(_html_escape(item["question"]), content)


class FaqPageAuthenticatedTest(TestCase):
    """A logged-in user gets the same standalone page (no dashboard
    redirect, no gating)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="faq-user@test.com",
            password="TestPass123!",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_returns_200(self):
        response = self.client.get("/faq")
        self.assertEqual(response.status_code, 200)

    def test_renders_every_faq_question(self):
        response = self.client.get("/faq")
        content = response.content.decode()
        for item in FAQ_ITEMS:
            # Apostrophes ("What's included...") are HTML-escaped to &#x27;
            # by Django's default autoescape, so escape the needle as well.
            self.assertIn(_html_escape(item["question"]), content)


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
        response = self.client.get("/faq")
        content = response.content.decode()
        # On the standalone page the wrapping section also uses id="faq" by
        # default; the test guards against accidentally renaming the id.
        match = re.search(r'<section id="faq"', content)
        self.assertIsNotNone(
            match, "FAQ partial should render <section id=\"faq\">"
        )
