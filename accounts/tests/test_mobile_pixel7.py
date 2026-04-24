"""Tests for Pixel 7 (412px) responsive audit (issue #182).

Verifies that:
- /accounts/signup/ redirects to /accounts/register/ (styled page)
- Tag links have min-h-[44px] for touch targets
- Social icon links on about page are at least 44px
- Course unit links have adequate touch target sizing
"""

import re

from django.test import TestCase


class SignupRedirectTest(TestCase):
    """The allauth /accounts/signup/ URL redirects to our styled register page."""

    def test_signup_redirects_to_register(self):
        """GET /accounts/signup/ returns 302 redirect to /accounts/register/."""
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/register/")

    def test_register_page_renders_styled(self):
        """GET /accounts/register/ returns 200 with styled form."""
        response = self.client.get("/accounts/register/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Account")
        self.assertContains(response, 'id="register-email"')


class BlogTagTouchTargetTest(TestCase):
    """Blog list tag links have min-h-[44px] for adequate touch targets."""

    def test_blog_list_tag_links_have_min_height(self):
        """Tag links in the blog list template include min-h-[44px]."""
        from django.template.loader import get_template

        template = get_template("content/blog_list.html")
        source = template.template.source
        # Find the tag link pattern (the one with tag_add_url)
        tag_link_match = re.search(r'tag_add_url.*?class="([^"]*)"', source)
        self.assertIsNotNone(tag_link_match, "Tag link not found in blog_list.html")
        self.assertIn("min-h-[44px]", tag_link_match.group(1))


class RecordingsTagTouchTargetTest(TestCase):
    """Past-recording tag chips on /events?filter=past have min-h-[44px]
    for touch targets (folded into events/events_list.html as of #294).
    """

    def test_past_recording_tag_links_have_min_height(self):
        """Tag links on the past-recordings section include min-h-[44px]."""
        from datetime import timedelta

        from django.utils import timezone

        from events.models import Event

        Event.objects.create(
            title='Tagged Recording',
            slug='tagged-recording',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            tags=['python'],
            published=True,
        )
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The tag chip href is used to identify the link, then we check that
        # its class contains the min-h-[44px] tap target.
        tag_link_match = re.search(
            r'<a[^>]*href="/events\?filter=past&amp;tag=python"[^>]*class="([^"]*)"',
            content,
        )
        self.assertIsNotNone(
            tag_link_match,
            "Past-recording tag link not found in events_list.html",
        )
        self.assertIn("min-h-[44px]", tag_link_match.group(1))


class AboutPageSocialIconTest(TestCase):
    """About page social icon links have h-11 w-11 (44px) sizing."""

    def test_social_icons_are_44px(self):
        """LinkedIn icons on about page use h-11 w-11 (44px)."""
        response = self.client.get("/about")
        content = response.content.decode()
        linkedin_links = re.findall(
            r'aria-label="LinkedIn"[^>]*class="([^"]*)"', content
        )
        # If class comes before aria-label
        if not linkedin_links:
            linkedin_links = re.findall(
                r'class="([^"]*)"[^>]*aria-label="LinkedIn"', content
            )
        self.assertTrue(len(linkedin_links) >= 2, "Expected at least 2 LinkedIn links")
        for classes in linkedin_links:
            self.assertIn("h-11", classes, "LinkedIn icon should be h-11 (44px)")
            self.assertIn("w-11", classes, "LinkedIn icon should be w-11 (44px)")


class CourseUnitTouchTargetTest(TestCase):
    """Course unit rows have min-h-[44px] for touch targets."""

    def test_unit_row_has_min_height(self):
        """Unit row container in course detail uses min-h-[44px]."""
        from django.template.loader import get_template

        template = get_template("content/course_detail.html")
        source = template.template.source
        # The unit row div has min-h-[44px]
        self.assertIn("min-h-[44px]", source)

    def test_unit_link_has_padding(self):
        """Unit link in course detail has py-2 for adequate touch area."""
        from django.template.loader import get_template

        template = get_template("content/course_detail.html")
        source = template.template.source
        # Find the unit link (the one with get_absolute_url)
        link_match = re.search(
            r'get_absolute_url.*?class="([^"]*)"', source
        )
        self.assertIsNotNone(link_match, "Unit link not found in course_detail.html")
        self.assertIn("py-2", link_match.group(1))
