"""Header/footer/about pricing and FAQ links."""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


def _extract_header(html):
    match = re.search(r"<header[\s\S]*?</header>", html)
    assert match, "No <header> element found in response"
    return match.group(0)


def _extract_footer(html):
    match = re.search(r"<footer[\s\S]*?</footer>", html)
    assert match, "No <footer> element found in response"
    return match.group(0)


def _extract_desktop_primary_nav(header):
    start = header.index('data-testid="desktop-primary-nav"')
    end = header.index('<div class="hidden md:flex md:items-center md:gap-4">')
    return header[start:end]


class HeaderLinksAnonymousTest(TestCase):
    """Anonymous user lands on the marketing homepage -- header still
    points at real pages, not anchors."""

    @classmethod
    def setUpTestData(cls):
        # No fixtures; the anonymous homepage renders without DB content.
        pass

    def test_membership_is_primary_header_nav(self):
        response = self.client.get("/")
        header = _extract_header(response.content.decode())
        primary = _extract_desktop_primary_nav(header)
        # Membership appears as a top-level link AND inside the Community
        # dropdown (per #580 grooming). Both must point at /pricing.
        membership_links = re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*Membership\s*</a>', primary
        )
        self.assertTrue(membership_links)
        self.assertTrue(all(href == "/pricing" for href in membership_links))
        self.assertNotIn('id="learn-dropdown-btn"', primary)
        self.assertIn('id="community-dropdown-btn"', primary)
        self.assertIn('id="resources-dropdown-btn"', primary)

    def test_faq_is_primary_header_link(self):
        response = self.client.get("/")
        header = _extract_header(response.content.decode())
        primary = _extract_desktop_primary_nav(header)
        faq_links = re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*FAQ\s*</a>', primary
        )
        self.assertEqual(faq_links, ["/faq"])


class HeaderLinksAuthenticatedTest(TestCase):
    """Logged-in user gets the dashboard at `/`, but header still has
    valid Membership/FAQ links."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="header-user@test.com",
            password="TestPass123!",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_authenticated_header_has_membership_text_nav(self):
        # Use a non-home page (about) so the dashboard does not interfere
        # with the test of the header partial.
        response = self.client.get("/about")
        header = _extract_header(response.content.decode())
        primary = _extract_desktop_primary_nav(header)
        # Membership appears as a top-level link AND inside the Community
        # dropdown (per #580 grooming). Both must point at /pricing.
        membership_links = re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*Membership\s*</a>', primary
        )
        self.assertTrue(membership_links)
        self.assertTrue(all(href == "/pricing" for href in membership_links))
        self.assertNotIn('id="learn-dropdown-btn"', primary)
        self.assertIn('id="community-dropdown-btn"', primary)
        self.assertIn('id="resources-dropdown-btn"', primary)

    def test_authenticated_header_has_primary_faq_link(self):
        response = self.client.get("/about")
        header = _extract_header(response.content.decode())
        primary = _extract_desktop_primary_nav(header)
        faq_links = re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*FAQ\s*</a>', primary
        )
        self.assertEqual(faq_links, ["/faq"])


class FooterLinksTest(TestCase):
    """Footer Membership Tiers + FAQ links go to real pages, not anchors."""

    def test_membership_tiers_points_to_pricing(self):
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        match = re.search(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*Membership Tiers\s*</a>', footer
        )
        self.assertIsNotNone(match, "Membership Tiers link missing in footer")
        self.assertEqual(match.group(1), "/pricing")

    def test_faq_link_points_to_standalone_faq_page(self):
        response = self.client.get("/")
        footer = _extract_footer(response.content.decode())
        match = re.search(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*FAQ\s*</a>', footer
        )
        self.assertIsNotNone(match, "FAQ link missing in footer")
        self.assertEqual(match.group(1), "/faq")


class AboutPageMembershipCtaTest(TestCase):
    """About page CTA goes to /pricing, not the marketing-only anchor."""

    def test_view_membership_tiers_cta_points_to_pricing(self):
        response = self.client.get("/about")
        content = response.content.decode()
        match = re.search(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*View Membership Tiers\s*</a>',
            content,
        )
        self.assertIsNotNone(
            match, "View Membership Tiers CTA missing on about page"
        )
        self.assertEqual(match.group(1), "/pricing")


class HomepageTiersAnchorStillWorksTest(TestCase):
    """The marketing homepage `#tiers` anchor + the `View Membership Tiers`
    same-page CTA still work for the anon flow."""

    def test_homepage_has_tiers_section_anchor(self):
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('id="tiers"', content)

    def test_homepage_anon_flow_cta_still_uses_anchor(self):
        # The CTA above the fold (`templates/home.html:51`) is intentionally
        # kept as an anchor so it scrolls within the same page. Guard against
        # accidental change that would break the in-page scroll behaviour.
        response = self.client.get("/")
        content = response.content.decode()
        # Look for the CTA (the same text appears on /about which now uses
        # /pricing -- here we restrict to the homepage response).
        match = re.search(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*View Membership Tiers\s*(?:<i[^>]*></i>\s*)?</a>',
            content,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "/#tiers")
