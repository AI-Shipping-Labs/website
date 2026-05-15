"""Tests for footer newsletter suppression on free-registration surfaces.

Issue #653. On pages that already invite the visitor to register for
free (the inline registration form from #652), the footer
"Build AI in public, with a group." newsletter block creates a competing
form. Free registration already implies newsletter opt-in by default,
so a second subscribe form on the same surface is redundant.

The suppression is achieved by a per-view ``hide_footer_newsletter``
context flag that the footer template ANDs onto the existing
``not user.is_authenticated`` gate. These tests pin:

- Footer newsletter is HIDDEN on /courses/<slug> (free-anon),
  /workshops/<slug> (pages paywall, anon), and /pricing (anon).
- Footer newsletter STILL renders on /blog and / (regression guards
  against over-suppression).
- The inline registration card carries the new opt-in disclosure copy
  on the in-scope surfaces.
- The standalone /accounts/register/ page does NOT carry the inline
  disclosure copy (the line is specific to inline-form contexts).
- The auth gate evaluation order is preserved: an authenticated user
  on a suppressed surface still does not see the newsletter (the
  ``and`` operator short-circuits on the existing
  ``not user.is_authenticated`` check before evaluating the new flag).
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Course, Module, Unit, Workshop, WorkshopPage
from tests.fixtures import TierSetupMixin

User = get_user_model()


# Locked copy from the footer block — duplicated here verbatim so a
# regression flips a test instead of silently drifting copy.
FOOTER_NEWSLETTER_ANCHOR = 'id="newsletter"'
FOOTER_NEWSLETTER_HEADING = 'Build AI in public, with a group.'
INLINE_OPT_IN_DISCLOSURE = (
    "By signing up free, you'll receive community updates. "
    "You can unsubscribe at any time."
)


class FooterNewsletterSuppressionTest(TierSetupMixin, TestCase):
    """Validate the ``hide_footer_newsletter`` per-view flag plus the
    inline opt-in disclosure copy.

    Fixtures cover the three in-scope surfaces:

    - ``free_course`` — published free course at /courses/free-101.
    - ``anon_workshop`` — published workshop at /workshops/anon-ws with
      ``pages_required_level=5`` (REGISTERED) so the pages paywall
      triggers the inline register card for anonymous viewers.
    - /pricing has no fixture requirement; it always renders the free
      tier card for anonymous visitors.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_course = Course.objects.create(
            title='Free Course',
            slug='free-101',
            status='published',
            required_level=0,
            description='A free course used by suppression tests.',
        )
        module = Module.objects.create(
            course=cls.free_course,
            title='Module',
            slug='module',
            sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Lesson', slug='lesson', sort_order=1,
        )
        cls.anon_workshop = Workshop.objects.create(
            slug='anon-ws',
            title='Anonymous Workshop',
            status='published',
            date=date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=5,
            recording_required_level=20,
            description='Workshop fixture for footer suppression tests.',
        )
        WorkshopPage.objects.create(
            workshop=cls.anon_workshop,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='Tutorial body.',
        )

    # ----------------------------------------------------------------
    # Suppressed surfaces — newsletter block must be ABSENT
    # ----------------------------------------------------------------

    def test_course_detail_anonymous_hides_footer_newsletter(self):
        """Anonymous GET /courses/free-101 renders without the footer
        newsletter block."""
        response = self.client.get('/courses/free-101')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FOOTER_NEWSLETTER_ANCHOR)
        self.assertNotContains(response, FOOTER_NEWSLETTER_HEADING)

    def test_workshop_detail_anonymous_hides_footer_newsletter(self):
        """Anonymous GET /workshops/anon-ws (pages paywall branch)
        renders without the footer newsletter block."""
        response = self.client.get('/workshops/anon-ws')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FOOTER_NEWSLETTER_ANCHOR)
        self.assertNotContains(response, FOOTER_NEWSLETTER_HEADING)

    def test_pricing_anonymous_hides_footer_newsletter(self):
        """Anonymous GET /pricing renders without the footer newsletter
        block — the free-tier card already carries the inline form."""
        response = self.client.get('/pricing')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FOOTER_NEWSLETTER_ANCHOR)
        self.assertNotContains(response, FOOTER_NEWSLETTER_HEADING)

    # ----------------------------------------------------------------
    # Positive regression guards — newsletter MUST still appear
    # ----------------------------------------------------------------

    def test_blog_anonymous_still_shows_footer_newsletter(self):
        """Anonymous GET /blog keeps the footer newsletter block. Blog
        is a discovery surface (no inline register form), so the flag
        must not over-suppress."""
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, FOOTER_NEWSLETTER_ANCHOR)
        self.assertContains(response, FOOTER_NEWSLETTER_HEADING)

    def test_home_anonymous_still_shows_footer_newsletter(self):
        """Anonymous GET / keeps the footer newsletter block. The
        homepage is the canonical newsletter placement."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, FOOTER_NEWSLETTER_ANCHOR)
        self.assertContains(response, FOOTER_NEWSLETTER_HEADING)

    # ----------------------------------------------------------------
    # Inline opt-in disclosure copy
    # ----------------------------------------------------------------

    def test_inline_form_shows_opt_in_disclosure(self):
        """The inline register card on an in-scope surface (course
        detail free-anon branch) carries the new opt-in disclosure
        line so the implicit "free signup = newsletter opt-in"
        becomes explicit."""
        response = self.client.get('/courses/free-101')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, INLINE_OPT_IN_DISCLOSURE)
        # The disclosure is wrapped in a labeled element for the
        # Playwright suite to locate.
        self.assertContains(
            response, 'data-testid="inline-register-opt-in"',
        )

    def test_register_page_does_not_show_inline_opt_in_disclosure(self):
        """The standalone /accounts/register/ page must NOT render the
        inline-only opt-in disclosure line. That page uses the
        ``_auth_card.html`` + ``_register_form.html`` chain directly,
        not the inline register card partial."""
        response = self.client.get('/accounts/register/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, INLINE_OPT_IN_DISCLOSURE)
        self.assertNotContains(
            response, 'data-testid="inline-register-opt-in"',
        )

    # ----------------------------------------------------------------
    # Gate evaluation order — authed user on suppressed surface
    # ----------------------------------------------------------------

    def test_authenticated_user_on_suppressed_surface_unaffected(self):
        """Logged-in users were already invisible to the footer
        newsletter (the existing ``not user.is_authenticated`` gate).
        Logging in as a free user, GET /courses/free-101 must still
        hide the block — this guards the AND order so the new flag
        doesn't accidentally re-enable the block for authed users."""
        user = User.objects.create_user(
            email='free@test.com', password='testpass',
        )
        user.tier = self.free_tier
        user.save(update_fields=['tier'])
        self.client.force_login(user)
        response = self.client.get('/courses/free-101')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FOOTER_NEWSLETTER_ANCHOR)
        self.assertNotContains(response, FOOTER_NEWSLETTER_HEADING)
