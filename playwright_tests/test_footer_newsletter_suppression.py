"""Playwright E2E coverage for footer newsletter suppression (#653).

The footer "Build AI in public, with a group." newsletter block is
suppressed on the three free-registration surfaces where the inline
register card from #652 already invites the visitor to sign up:

- /courses/<slug>           (free course + anonymous visitor)
- /workshops/<slug>         (pages-level paywall on a registered wall)
- /pricing                  (free-tier card, anonymous visitor)

Discovery pages (/, /blog) keep the footer block — they don't render
an inline register form and the newsletter is the only signup CTA.

Each scenario walks an anonymous visitor through one surface and
asserts on user-visible behavior (the heading is absent / present, the
``#newsletter`` anchor is absent / present, the inline form is the
only signup mechanism, the disclosure copy is visible).
"""

from datetime import date

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
)

FOOTER_HEADING = 'Build AI in public, with a group.'
INLINE_OPT_IN_COPY = (
    "By signing up free, you'll receive community updates. "
    "You can unsubscribe at any time."
)


def _reset_state():
    """Clear all fixtures the suppression suite touches.

    Caller must hold django_db_blocker.unblock() — closes the connection
    at the end so the Django server thread can read.
    """
    from django.db import connection

    from accounts.models import User
    from content.models import Course, Module, Unit, Workshop, WorkshopPage

    Unit.objects.filter(module__course__slug__startswith='supp-653').delete()
    Module.objects.filter(course__slug__startswith='supp-653').delete()
    Course.objects.filter(slug__startswith='supp-653').delete()
    WorkshopPage.objects.filter(
        workshop__slug__startswith='supp-653',
    ).delete()
    Workshop.objects.filter(slug__startswith='supp-653').delete()
    User.objects.filter(email__endswith='@supp-653.test').delete()
    connection.close()


def _seed_free_course(slug='supp-653-course'):
    from django.db import connection

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title='Free AI 101',
        slug=slug,
        status='published',
        required_level=0,
        description='Free course for footer-suppression E2E.',
    )
    module = Module.objects.create(
        course=course, title='Module', slug='module', sort_order=1,
    )
    Unit.objects.create(
        module=module, title='Lesson', slug='lesson', sort_order=1,
    )
    connection.close()
    return course, slug


def _seed_anon_workshop(slug='supp-653-workshop'):
    from django.db import connection

    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug=slug,
        title='Anon Pages Workshop',
        status='published',
        date=date(2026, 4, 21),
        landing_required_level=0,
        pages_required_level=5,
        recording_required_level=20,
        description='Workshop for footer-suppression E2E.',
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='intro', title='Intro', sort_order=1,
        body='Tutorial body.',
    )
    connection.close()
    return workshop, slug


@pytest.mark.django_db(transaction=True)
class TestFooterNewsletterSuppression:
    """Eight BDD scenarios pinned to the issue body.

    Three suppression surfaces, two positive regression guards, two
    copy-disclosure assertions, and one authed-user gate-order guard.
    """

    # ----------------------------------------------------------------
    # Suppression surfaces
    # ----------------------------------------------------------------

    def test_anonymous_visitor_on_free_course_sees_one_signup_not_two(
        self, django_server, page, django_db_blocker,
    ):
        """Anonymous visitor on a free course page sees the inline
        register form and NO footer newsletter block."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()

        page.goto(
            f'{django_server}/courses/supp-653-course',
            wait_until='domcontentloaded',
        )
        # Inline register card is the visible signup form.
        assert page.locator(
            "[data-testid='inline-register-card']",
        ).is_visible()
        # Footer newsletter heading is absent — count returns 0.
        assert page.get_by_role(
            'heading', name=FOOTER_HEADING, exact=True,
        ).count() == 0
        # The newsletter anchor is absent from the rendered DOM.
        assert page.locator('#newsletter').count() == 0
        # No second subscribe form posting to /api/subscribe is on the
        # page (the footer form is the only such form site-wide).
        assert page.locator('form.subscribe-form').count() == 0

    def test_anonymous_visitor_on_pricing_sees_no_footer_newsletter(
        self, django_server, page, django_db_blocker,
    ):
        """Anonymous visitor on /pricing sees no footer newsletter
        block, but the footer's site map / copyright still render."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/pricing', wait_until='domcontentloaded')
        # Footer newsletter block is gone.
        assert page.get_by_role(
            'heading', name=FOOTER_HEADING, exact=True,
        ).count() == 0
        assert page.locator('#newsletter').count() == 0
        # The rest of the footer still renders — Community + Legal
        # columns plus the copyright line stay visible. The header
        # text is unique to the footer site map.
        assert page.get_by_role(
            'heading', name='Community', exact=True,
        ).is_visible()
        assert page.get_by_role(
            'heading', name='Legal', exact=True,
        ).is_visible()
        assert page.get_by_text('AI Shipping Labs').first.is_visible()

    def test_anonymous_visitor_on_workshop_pages_paywall_sees_no_footer_newsletter(
        self, django_server, page, django_db_blocker,
    ):
        """Anonymous visitor on a registered-wall workshop landing sees
        the inline register card and no footer newsletter block."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_anon_workshop()

        page.goto(
            f'{django_server}/workshops/supp-653-workshop',
            wait_until='domcontentloaded',
        )
        # Inline register card renders (pages paywall + signup CTA).
        assert page.locator(
            "[data-testid='inline-register-card']",
        ).is_visible()
        # Footer newsletter is gone.
        assert page.get_by_role(
            'heading', name=FOOTER_HEADING, exact=True,
        ).count() == 0
        assert page.locator('#newsletter').count() == 0
        # Footer site map remains.
        assert page.get_by_role(
            'heading', name='Community', exact=True,
        ).is_visible()

    # ----------------------------------------------------------------
    # Positive regression guards — newsletter MUST still appear
    # ----------------------------------------------------------------

    def test_anonymous_visitor_on_blog_still_sees_footer_newsletter(
        self, django_server, page, django_db_blocker,
    ):
        """Anonymous visitor on /blog still sees the footer newsletter
        block (regression guard against over-suppression)."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/blog', wait_until='domcontentloaded')
        # Newsletter heading + anchor both present.
        assert page.get_by_role(
            'heading', name=FOOTER_HEADING, exact=True,
        ).is_visible()
        assert page.locator('#newsletter').count() == 1
        # The subscribe form exists and posts to /api/subscribe (its
        # only target).
        assert page.locator('form.subscribe-form').count() == 1

    def test_anonymous_visitor_on_home_still_sees_footer_newsletter(
        self, django_server, page, django_db_blocker,
    ):
        """Anonymous visitor on / still sees the footer newsletter
        block — the homepage is the canonical anonymous CTA."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        assert page.get_by_role(
            'heading', name=FOOTER_HEADING, exact=True,
        ).is_visible()
        assert page.locator('#newsletter').count() == 1
        assert page.locator('form.subscribe-form').count() == 1

    # ----------------------------------------------------------------
    # Inline opt-in disclosure copy
    # ----------------------------------------------------------------

    def test_inline_form_discloses_implicit_newsletter_opt_in(
        self, django_server, page, django_db_blocker,
    ):
        """The inline register card on /courses/<slug> renders the new
        opt-in disclosure line near the submit button."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()

        page.goto(
            f'{django_server}/courses/supp-653-course',
            wait_until='domcontentloaded',
        )
        # The disclosure is located inside the inline register card.
        inline_card = page.locator("[data-testid='inline-register-card']")
        assert inline_card.is_visible()
        disclosure = inline_card.locator(
            "[data-testid='inline-register-opt-in']",
        )
        assert disclosure.is_visible()
        assert INLINE_OPT_IN_COPY in disclosure.inner_text()

    def test_standalone_register_page_does_not_show_opt_in_disclosure(
        self, django_server, page, django_db_blocker,
    ):
        """The standalone /accounts/register/ page must not show the
        inline-only opt-in disclosure line."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(
            f'{django_server}/accounts/register/',
            wait_until='domcontentloaded',
        )
        # The register form is the standalone variant (uses _auth_card,
        # not _inline_register_card).
        assert page.locator('#register-email').is_visible()
        # No inline-card wrapper and no disclosure copy.
        assert page.locator(
            "[data-testid='inline-register-card']",
        ).count() == 0
        assert page.locator(
            "[data-testid='inline-register-opt-in']",
        ).count() == 0

    # ----------------------------------------------------------------
    # Authed user on suppressed surface — gate evaluation order guard
    # ----------------------------------------------------------------

    def test_authenticated_user_on_suppressed_surface_sees_no_newsletter(
        self, django_server, browser, django_db_blocker,
    ):
        """A free authenticated user on /courses/<slug> sees no
        newsletter block (it was already absent for authed users; this
        guards the AND gate so the new flag's presence doesn't
        accidentally re-enable the block for authed users)."""
        email = 'free@supp-653.test'
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            create_user(email=email, tier_slug='free')

        context = auth_context(browser, email)
        page = context.new_page()
        try:
            page.goto(
                f'{django_server}/courses/supp-653-course',
                wait_until='domcontentloaded',
            )
            assert page.get_by_role(
                'heading', name=FOOTER_HEADING, exact=True,
            ).count() == 0
            assert page.locator('#newsletter').count() == 0
        finally:
            context.close()
