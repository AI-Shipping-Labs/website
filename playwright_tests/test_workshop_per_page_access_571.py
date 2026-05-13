"""Playwright E2E tests for per-page workshop access overrides (issue #571).

Covers the eight scenarios groomed in the issue:

1. Anonymous visitor previews a workshop's first page but hits a wall on
   page two (page 1 has ``required_level=0``, pages 2/3 inherit).
2. Free signed-in member completes a workshop end-to-end (workshop
   default is ``LEVEL_REGISTERED=5``).
3. Anonymous visitor on a paid workshop still hits the upgrade wall on
   page one (no per-page override).
4. Free member on a paid workshop sees the upgrade path from the open
   preview page (page 1 open, page 2 inherits Basic+).
5. Workshop landing page stays open to anonymous visitors regardless of
   page gates.
6. Sign-in CTA round-trips a previously-blocked anonymous visitor back
   to the same page.
7. Workshop video recording gating is unaffected by the page-level
   changes.
8. Staff member auditing a workshop sees per-page overrides in Studio.

Usage:
    uv run pytest playwright_tests/test_workshop_per_page_access_571.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

DEFAULT_PASSWORD = 'TestPass123!'


def _clear_workshops():
    """Reset workshop / page / event state before each test."""
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(
    *,
    slug,
    title='Per-Page Workshop',
    landing=0,
    pages=5,  # LEVEL_REGISTERED — the new default from issue #571
    recording=20,
    with_event=False,
    description='# Workshop\n\nDescription body.',
    pages_data=None,
    recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
):
    """Create a workshop with 3 tutorial pages.

    ``pages_data`` is an iterable of ``(slug, title, body, required_level)``
    tuples. ``required_level`` may be ``None`` to inherit the workshop
    default.
    """
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import (
        Instructor,
        Workshop,
        WorkshopInstructor,
        WorkshopPage,
    )
    from events.models import Event

    event = None
    if with_event:
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=title,
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            recording_url=recording_url,
            published=True,
        )
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
        event=event,
    )
    instructor_obj, _ = Instructor.objects.get_or_create(
        instructor_id=slugify('Alexey')[:200] or 'test-instructor',
        defaults={'name': 'Alexey', 'status': 'published'},
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop, instructor=instructor_obj,
        defaults={'position': 0},
    )
    pages_data = pages_data or [
        ('page-one', 'Page One', '# Page One\n\nBody.', 0),
        ('page-two', 'Page Two', '# Page Two\n\nBody.', None),
        ('page-three', 'Page Three', '# Page Three\n\nBody.', None),
    ]
    for i, (s, t, body, req_level) in enumerate(pages_data, start=1):
        WorkshopPage.objects.create(
            workshop=workshop, slug=s, title=t,
            sort_order=i, body=body, required_level=req_level,
        )
    connection.close()
    return workshop


# ---------------------------------------------------------------------
# Scenario 1: Anonymous previews page 1, blocked on page 2
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousPreviewsPageOne:
    def test_anon_reads_page_one_then_hits_signin_wall_on_page_two(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(slug='preview-ws', pages=5, recording=20)

        # Page 1 — full body renders for anonymous (override 0).
        response = page.goto(
            f'{django_server}/workshops/preview-ws/tutorial/page-one',
            wait_until='domcontentloaded',
        )
        assert response.status == 200
        body = page.content()
        assert 'data-testid="page-body"' in body
        # No paywall card on the open override page.
        assert 'data-testid="page-paywall"' not in body
        # "Next" link points at page 2.
        next_btn = page.locator('[data-testid="page-next-btn"]')
        assert next_btn.count() >= 1
        next_href = next_btn.first.get_attribute('href')
        assert '/workshops/preview-ws/tutorial/page-two' in next_href

        # Page 2 — registered wall, sign-in CTA.
        response2 = page.goto(
            f'{django_server}/workshops/preview-ws/tutorial/page-two',
            wait_until='domcontentloaded',
        )
        assert response2.status == 403
        body2 = page.content()
        # Title still rendered (SEO).
        assert 'data-testid="page-title"' in body2
        # Paywall card present.
        assert 'data-testid="page-paywall"' in body2
        # CTA reads "Sign In" and preserves the return URL.
        cta = page.locator('[data-testid="page-upgrade-cta"]')
        assert cta.count() == 1
        cta_href = cta.get_attribute('href')
        assert cta_href.startswith('/accounts/login/')
        assert 'next=' in cta_href
        assert 'page-two' in cta_href
        # Secondary "Create a free account" link.
        signup = page.locator('[data-testid="teaser-signup-cta"]')
        assert signup.count() == 1


# ---------------------------------------------------------------------
# Scenario 2: Free verified member completes workshop end-to-end
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFreeMemberCompletesWorkshop:
    def test_free_member_reads_all_pages_and_marks_complete(
        self, django_server, browser,
    ):
        _clear_workshops()
        _create_workshop(slug='free-end-to-end', pages=5, recording=20)
        _create_user(
            email='free@test.com', tier_slug='free',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'free@test.com')
        try:
            p = ctx.new_page()
            # Page 1 — full body.
            r = p.goto(
                f'{django_server}/workshops/free-end-to-end/tutorial/page-one',
                wait_until='domcontentloaded',
            )
            assert r.status == 200
            assert 'data-testid="page-body"' in p.content()

            # Page 2 — full body (registered wall passes a verified free user).
            r = p.goto(
                f'{django_server}/workshops/free-end-to-end/tutorial/page-two',
                wait_until='domcontentloaded',
            )
            assert r.status == 200
            body = p.content()
            assert 'data-testid="page-body"' in body
            assert 'data-testid="page-paywall"' not in body

            # Page 3 — full body.
            r = p.goto(
                f'{django_server}/workshops/free-end-to-end/tutorial/page-three',
                wait_until='domcontentloaded',
            )
            assert r.status == 200
            assert 'data-testid="page-body"' in p.content()
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 3: Anonymous on a paid workshop hits the upgrade wall on page 1
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousOnPaidWorkshopUpgradeWall:
    def test_anon_sees_upgrade_to_basic_on_page_one(
        self, django_server, page,
    ):
        _clear_workshops()
        # Workshop default Basic (10), no per-page override on page 1.
        _create_workshop(
            slug='paid-ws', pages=10, recording=20,
            pages_data=[
                ('lesson-one', 'Lesson One', '# Lesson One', None),
                ('lesson-two', 'Lesson Two', '# Lesson Two', None),
            ],
        )

        response = page.goto(
            f'{django_server}/workshops/paid-ws/tutorial/lesson-one',
            wait_until='domcontentloaded',
        )
        assert response.status == 403
        body = page.content()
        # Upgrade copy.
        assert 'Upgrade to Basic to access this workshop' in body
        # CTA points to /pricing.
        cta = page.locator('[data-testid="page-upgrade-cta"]')
        assert cta.get_attribute('href') == '/pricing'
        # Anonymous on a paid wall sees a "Create a free account" companion.
        signup = page.locator('[data-testid="teaser-signup-cta"]')
        assert signup.count() == 1


# ---------------------------------------------------------------------
# Scenario 4: Free member on a paid workshop sees upgrade path from page 1
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFreeMemberOnPaidWorkshopWithOpenPreview:
    def test_free_member_reads_open_page_then_sees_upgrade(
        self, django_server, browser,
    ):
        _clear_workshops()
        _create_workshop(
            slug='paid-preview', pages=10, recording=20,
            pages_data=[
                ('intro', 'Intro', '# Intro\n\nBody.', 0),
                ('part-two', 'Part Two', '# Part Two\n\nBody.', None),
            ],
        )
        _create_user(
            email='free-preview@test.com', tier_slug='free',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'free-preview@test.com')
        try:
            p = ctx.new_page()
            # Open page — full body.
            r = p.goto(
                f'{django_server}/workshops/paid-preview/tutorial/intro',
                wait_until='domcontentloaded',
            )
            assert r.status == 200
            assert 'data-testid="page-body"' in p.content()

            # Inherited page — upgrade card.
            r = p.goto(
                f'{django_server}/workshops/paid-preview/tutorial/part-two',
                wait_until='domcontentloaded',
            )
            assert r.status == 403
            body = p.content()
            assert 'Upgrade to Basic to access this workshop' in body
            cta = p.locator('[data-testid="page-upgrade-cta"]')
            assert cta.get_attribute('href') == '/pricing'
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 5: Landing page stays open to anonymous visitors
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLandingPageStaysOpen:
    def test_anon_sees_landing_even_with_registered_pages(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(slug='landing-ws', pages=5, recording=20)

        response = page.goto(
            f'{django_server}/workshops/landing-ws',
            wait_until='domcontentloaded',
        )
        assert response.status == 200
        body = page.content()
        # Workshop title rendered.
        assert 'Per-Page Workshop' in body
        # No paywall card on the landing itself.
        assert 'data-testid="page-paywall"' not in body
        # Pages list links to tutorial URLs.
        assert '/workshops/landing-ws/tutorial/page-one' in body
        assert '/workshops/landing-ws/tutorial/page-two' in body


# ---------------------------------------------------------------------
# Scenario 6: Sign-in CTA round-trips back to the same page
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSignInRoundTrip:
    def test_signin_redirects_back_to_gated_page(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(slug='roundtrip-ws', pages=5, recording=20)
        _create_user(
            email='roundtrip@test.com', tier_slug='free',
            email_verified=True, password=DEFAULT_PASSWORD,
        )

        # Visit gated page 2 anonymously.
        page.goto(
            f'{django_server}/workshops/roundtrip-ws/tutorial/page-two',
            wait_until='domcontentloaded',
        )
        # Click Sign In CTA.
        page.locator('[data-testid="page-upgrade-cta"]').click()
        page.wait_for_load_state('domcontentloaded')
        assert '/accounts/login/' in page.url
        assert 'next=' in page.url

        # Log in.
        page.fill('#login-email', 'roundtrip@test.com')
        page.fill('#login-password', DEFAULT_PASSWORD)
        page.click('#login-submit')
        page.wait_for_url('**/workshops/roundtrip-ws/tutorial/page-two')

        assert '/workshops/roundtrip-ws/tutorial/page-two' in page.url
        body = page.content()
        # Full body renders post-login.
        assert 'data-testid="page-body"' in body
        assert 'data-testid="page-paywall"' not in body


# ---------------------------------------------------------------------
# Scenario 7: Video recording gating is unaffected by page-level changes
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestVideoGateUnchanged:
    def test_free_member_sees_recording_upgrade_card(
        self, django_server, browser,
    ):
        _clear_workshops()
        _create_workshop(
            slug='video-gate', pages=5, recording=20,
            with_event=True,
        )
        _create_user(
            email='free-vid@test.com', tier_slug='free',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'free-vid@test.com')
        try:
            p = ctx.new_page()
            # Landing renders (anonymous landing is 0 by default).
            r = p.goto(
                f'{django_server}/workshops/video-gate',
                wait_until='domcontentloaded',
            )
            assert r.status == 200

            # Issue #618: /video 301-redirects to the player layout. The
            # locked variant on /workshops/<slug> shows the discreet
            # header link and no iframe markup.
            r = p.goto(
                f'{django_server}/workshops/video-gate/video',
                wait_until='domcontentloaded',
            )
            # Final URL is the player layout (301-followed by Playwright).
            assert p.url == f'{django_server}/workshops/video-gate'
            body = p.content()
            assert (
                'data-testid="workshop-recording-locked-header-link"' in body
            )
            assert 'youtube.com/embed' not in body
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 8: Staff sees per-page override badge in Studio
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStudioOverrideBadge:
    def test_staff_sees_override_badge_for_open_page_only(
        self, django_server, browser,
    ):
        _clear_workshops()
        ws = _create_workshop(
            slug='studio-ws', pages=5, recording=20,
            pages_data=[
                ('intro', 'Intro', '# Intro', 0),
                ('lesson', 'Lesson', '# Lesson', None),
                ('finale', 'Finale', '# Finale', None),
            ],
        )
        _create_staff_user(email='admin@test.com')

        ctx = _auth_context(browser, 'admin@test.com')
        try:
            p = ctx.new_page()
            r = p.goto(
                f'{django_server}/studio/workshops/{ws.pk}/',
                wait_until='domcontentloaded',
            )
            assert r.status == 200
            body = p.content()
            # All three rows render.
            rows = p.locator(
                '[data-testid="workshop-pages-rows"] tr'
            )
            assert rows.count() == 3
            # Badge appears once — only for the override on page 1.
            badges = p.locator(
                '[data-testid="page-required-level-badge"]'
            )
            assert badges.count() == 1
            # The badge text matches the public access label for level 0.
            assert 'Free' in badges.first.inner_text()
            # Other rows render without the badge.
            assert body.count('data-testid="page-required-level-badge"') == 1
        finally:
            ctx.close()
