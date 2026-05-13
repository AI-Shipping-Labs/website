"""Playwright E2E tests for the workshop teaser-with-fade gating (#515).

Covers the nine scenarios groomed in the issue:

1. Anonymous visitor on a free-with-registration tutorial finds the
   sign-up path.
2. Free member on a Basic-gated tutorial sees the upgrade path.
3. Main member reads a Basic-gated tutorial in full (no teaser, no
   fade).
4. Anonymous on a paid recording sees the locked thumbnail and a
   sign-up companion link.
5. Free member on a Main-gated recording sees the upgrade card.
6. Premium member watches the same recording end-to-end.
7. Empty-body tutorial falls back to the bare paywall (no empty fade).
8. Sign-in from a gated tutorial returns the user to the same page.
9. Unverified-email user sees the verify-email card, not the teaser
   fade.

Usage:
    uv run pytest playwright_tests/test_workshop_teaser_gating.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

DEFAULT_PASSWORD = 'TestPass123!'

# Long markdown body so the teaser truncates ~150 words and we can
# assert the late marker is not visible to gated users.
_LONG_BODY_LINES = [
    'This first paragraph contains the unique phrase TUTORIALTEASERMARKER '
    'so the teaser can be checked end-to-end.',
]
_LONG_BODY_LINES.extend(
    f'Paragraph {i} sentence one with several distinct teaser words. '
    f'Paragraph {i} sentence two adds even more recognisable phrases.'
    for i in range(8)
)
_LONG_BODY_LINES.append(
    'TUTORIALHIDDENMARKER which should NOT appear in the teaser because '
    'it sits well past the 150-word cutoff and is only visible after a '
    'successful sign-in.'
)
_LONG_BODY = '\n\n'.join(_LONG_BODY_LINES)

_LONG_DESCRIPTION = (
    'This recording walks you through the WORKSHOPVIDEODESCMARKER '
    'pipeline in depth.\n\n'
    + '\n\n'.join(
        f'Paragraph {i} discusses architecture, failure modes, deployment '
        'trade-offs, and operational practices that turn a prototype into '
        'a production system.'
        for i in range(8)
    )
    + '\n\nWORKSHOPVIDEODESCHIDDEN past the 150-word fade-out cutoff.'
)


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(
    *,
    slug,
    title='Teaser Workshop',
    landing=0,
    pages=10,
    recording=20,
    with_event=True,
    description='Workshop description body.',
    pages_data=None,
    timestamps=None,
):
    """Create a workshop + linked event + tutorial pages."""
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
            recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            timestamps=timestamps or [],
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
        ('intro', 'Introduction', _LONG_BODY),
        ('setup', 'Setup', '## Setup\n\nInstall dependencies.'),
    ]
    for i, (s, t, body) in enumerate(pages_data, start=1):
        WorkshopPage.objects.create(
            workshop=workshop, slug=s, title=t,
            sort_order=i, body=body,
        )
    connection.close()
    return workshop


# ---------------------------------------------------------------------
# Scenario 1: Anonymous visitor → free-with-registration tutorial
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAnonRegisteredTutorial:
    def test_anon_finds_signup_path(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='reg-tut', pages=5, recording=10)

        page.goto(
            f'{django_server}/workshops/reg-tut/tutorial/intro',
            wait_until='domcontentloaded',
        )

        body = page.content()
        # Title and breadcrumb visible — page is SEO-indexable.
        assert 'data-testid="page-title"' in body
        assert 'data-testid="page-breadcrumb"' in body
        # Teaser body shows the early marker but hides the late one.
        assert 'TUTORIALTEASERMARKER' in body
        assert 'TUTORIALHIDDENMARKER' not in body
        # Fade wrapper is present.
        assert 'data-testid="teaser-body-wrapper"' in body
        # Sign-in card with both buttons.
        assert 'Sign In' in body
        assert 'Create a free account' in body

        # Hover over the Sign In primary button — should link to login
        # with next= preserved.
        login = page.locator('[data-testid="page-upgrade-cta"]')
        href = login.get_attribute('href')
        assert href is not None
        assert href.startswith('/accounts/login/')
        assert 'next=%2Fworkshops%2Freg-tut%2Ftutorial%2Fintro' in href

        # Click the secondary signup button.
        signup = page.locator('[data-testid="teaser-signup-cta"]')
        signup_href = signup.get_attribute('href')
        assert signup_href.startswith('/accounts/signup/')
        assert 'next=%2Fworkshops%2Freg-tut%2Ftutorial%2Fintro' in signup_href

        signup.click()
        page.wait_for_load_state('domcontentloaded')
        # /accounts/signup/ redirects to /accounts/register/ — both
        # preserve the next= parameter so the user lands back on the
        # tutorial after completing signup.
        assert '/accounts/register' in page.url or '/accounts/signup' in page.url
        assert 'next=' in page.url


# ---------------------------------------------------------------------
# Scenario 2: Free member → Basic-gated tutorial → upgrade path
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestFreeMemberOnBasicTutorial:
    def test_free_member_sees_upgrade_cta(
        self, django_server, browser,
    ):
        _clear_workshops()
        _create_workshop(slug='paid-tut', pages=10, recording=20)
        _create_user(
            email='free-paid@test.com', tier_slug='free',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'free-paid@test.com')
        try:
            page = ctx.new_page()
            page.goto(
                f'{django_server}/workshops/paid-tut/tutorial/intro',
                wait_until='domcontentloaded',
            )
            body = page.content()
            assert 'data-testid="page-title"' in body
            assert 'data-testid="teaser-body-wrapper"' in body
            assert 'TUTORIALTEASERMARKER' in body
            assert 'TUTORIALHIDDENMARKER' not in body

            # Tier badge reads "Basic or above required".
            assert 'Basic or above required' in body
            # Heading copy.
            assert 'Upgrade to Basic to access this workshop' in body
            # Single View Pricing CTA.
            cta = page.locator('[data-testid="page-upgrade-cta"]')
            assert cta.get_attribute('href') == '/pricing'
            # No "Create a free account" companion for signed-in users.
            signup = page.locator('[data-testid="teaser-signup-cta"]')
            assert signup.count() == 0
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 3: Main member → full tutorial body, no fade
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestMainMemberFullBody:
    def test_main_member_reads_full_body(self, django_server, browser):
        _clear_workshops()
        _create_workshop(slug='main-tut', pages=10, recording=20)
        _create_user(
            email='main-tut@test.com', tier_slug='main',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'main-tut@test.com')
        try:
            page = ctx.new_page()
            response = page.goto(
                f'{django_server}/workshops/main-tut/tutorial/intro',
                wait_until='domcontentloaded',
            )
            assert response.status == 200
            body = page.content()
            # Full body renders (page-body container, both markers).
            assert 'data-testid="page-body"' in body
            assert 'TUTORIALTEASERMARKER' in body
            assert 'TUTORIALHIDDENMARKER' in body
            # No fade wrapper, no upgrade card.
            assert 'data-testid="teaser-body-wrapper"' not in body
            assert 'data-testid="page-paywall"' not in body
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 4: Anonymous on a paid recording → locked thumbnail + signup
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAnonOnPaidRecording:
    def test_anon_redirected_to_player_layout_locked_variant(
        self, django_server, page,
    ):
        # Issue #618: /video 301-redirects to /workshops/<slug>. The
        # locked variant on the new player layout shows the discreet
        # header link, no iframe markup, and the chapter outline as
        # an informational syllabus.
        _clear_workshops()
        _create_workshop(
            slug='reg-vid',
            description=_LONG_DESCRIPTION,
            pages=10, recording=10,  # Basic-gated recording
        )

        page.goto(
            f'{django_server}/workshops/reg-vid/video',
            wait_until='domcontentloaded',
        )
        # Final URL is the new player layout.
        assert page.url == f'{django_server}/workshops/reg-vid'
        body = page.content()
        # No iframe markup.
        assert 'youtube.com/embed' not in body
        assert 'workshop_player.js' not in body
        # Discreet locked header link present, pointing at /pricing.
        link = page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        )
        assert link.count() == 1
        assert link.get_attribute('href') == '/pricing'


# ---------------------------------------------------------------------
# Scenario 5: Free member → Main-gated recording → upgrade card
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestFreeMemberOnMainRecording:
    def test_free_member_sees_locked_header_link_on_player_layout(
        self, django_server, browser,
    ):
        # Issue #618: free user on a Main-gated recording lands on the
        # new player layout (the /video URL 301s) with the locked
        # variant — discreet header link, no iframe markup.
        _clear_workshops()
        _create_workshop(
            slug='main-vid',
            description=_LONG_DESCRIPTION,
            pages=10, recording=20,
        )
        _create_user(
            email='free-vid@test.com', tier_slug='free',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'free-vid@test.com')
        try:
            page = ctx.new_page()
            page.goto(
                f'{django_server}/workshops/main-vid/video',
                wait_until='domcontentloaded',
            )
            assert page.url == f'{django_server}/workshops/main-vid'
            link = page.locator(
                '[data-testid="workshop-recording-locked-header-link"]',
            )
            assert link.count() == 1
            assert 'Get Main' in link.inner_text()
            assert link.get_attribute('href') == '/pricing'
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 6: Premium member → full embed + materials
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestPremiumMemberFullVideo:
    def test_premium_member_lands_on_player_layout_with_iframe(
        self, django_server, browser,
    ):
        # Issue #618: premium user on /video gets 301'd to the player
        # layout, which renders the player pane + the JS module.
        _clear_workshops()
        _create_workshop(
            slug='prem-vid',
            description=_LONG_DESCRIPTION,
            pages=10, recording=20,
        )
        _create_user(
            email='premium@test.com', tier_slug='premium',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'premium@test.com')
        try:
            page = ctx.new_page()
            page.goto(
                f'{django_server}/workshops/prem-vid/video',
                wait_until='domcontentloaded',
            )
            assert page.url == f'{django_server}/workshops/prem-vid'
            body = page.content()
            assert 'data-testid="workshop-player-pane"' in body
            assert 'data-testid="workshop-player-script"' in body
            # No locked variant artefacts.
            assert (
                'data-testid="workshop-recording-locked-header-link"'
                not in body
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 7: Empty-body tutorial → bare paywall (no empty fade)
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestEmptyBodyFallback:
    def test_empty_body_renders_bare_paywall(self, django_server, browser):
        _clear_workshops()
        _create_workshop(
            slug='empty-tut', pages=10, recording=20,
            pages_data=[('blank', 'Blank Page', '')],
        )
        _create_user(
            email='empty-free@test.com', tier_slug='free',
            email_verified=True,
        )

        ctx = _auth_context(browser, 'empty-free@test.com')
        try:
            page = ctx.new_page()
            page.goto(
                f'{django_server}/workshops/empty-tut/tutorial/blank',
                wait_until='domcontentloaded',
            )
            body = page.content()
            # Title and paywall card render.
            assert 'data-testid="page-title"' in body
            assert 'data-testid="page-paywall"' in body
            # No teaser-body wrapper / fade.
            assert 'data-testid="teaser-body"' not in body
            assert 'data-testid="teaser-body-wrapper"' not in body
        finally:
            ctx.close()


# ---------------------------------------------------------------------
# Scenario 8: Sign-in from gated tutorial returns to the same page
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSignInReturnsToTutorial:
    def test_signin_round_trip_lands_on_tutorial(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(slug='return-tut', pages=5, recording=10)
        _create_user(
            email='return-tut@test.com', tier_slug='free',
            email_verified=True,
            password=DEFAULT_PASSWORD,
        )

        # Visit the gated tutorial as anonymous and click Sign In.
        page.goto(
            f'{django_server}/workshops/return-tut/tutorial/intro',
            wait_until='domcontentloaded',
        )
        page.locator('[data-testid="page-upgrade-cta"]').click()
        page.wait_for_load_state('domcontentloaded')
        assert '/accounts/login/' in page.url
        assert 'next=' in page.url

        # Submit the login form. The form uses id-prefixed fields and a
        # JS-driven /api/login endpoint that redirects on success — see
        # templates/accounts/login.html.
        page.fill('#login-email', 'return-tut@test.com')
        page.fill('#login-password', DEFAULT_PASSWORD)
        page.click('#login-submit')
        # Wait for the JS redirect to complete.
        page.wait_for_url('**/workshops/return-tut/tutorial/intro')

        # Lands back on the tutorial page; full body renders.
        assert '/workshops/return-tut/tutorial/intro' in page.url
        body = page.content()
        assert 'data-testid="page-body"' in body
        assert 'TUTORIALHIDDENMARKER' in body
        assert 'data-testid="teaser-body-wrapper"' not in body


# ---------------------------------------------------------------------
# Scenario 9: Unverified-email user → verify-email card (no teaser)
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestUnverifiedEmailCard:
    def test_unverified_user_sees_verify_card(
        self, django_server, browser,
    ):
        _clear_workshops()
        _create_workshop(slug='verify-tut', pages=5, recording=10)
        _create_user(
            email='unverified-tut@test.com', tier_slug='free',
            email_verified=False,
        )

        ctx = _auth_context(browser, 'unverified-tut@test.com')
        try:
            page = ctx.new_page()
            page.goto(
                f'{django_server}/workshops/verify-tut/tutorial/intro',
                wait_until='domcontentloaded',
            )
            body = page.content()
            # Verify-email partial renders with a resend link.
            assert 'data-testid="verify-email-required-card"' in body
            assert 'Resend verification email' in body
            # No fade gradient and no Sign In/Create-a-free-account.
            assert 'data-testid="teaser-body-wrapper"' not in body
            assert 'data-testid="teaser-signup-cta"' not in body
        finally:
            ctx.close()
