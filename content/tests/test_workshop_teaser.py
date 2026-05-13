"""Tests for the workshop tutorial / video teaser-with-fade gating (#515).

Ports the course-unit teaser pattern from issue #248 to workshop tutorial
pages and the workshop video page. Adds support for ``LEVEL_REGISTERED``
on ``Workshop.pages_required_level`` so authors can require a free
account without requiring payment.

Covers:

* ``Workshop.pages_required_level`` accepts ``LEVEL_REGISTERED``.
* ``Workshop.user_can_access_pages`` honours the registered wall (anon
  denied, free verified allowed).
* Anonymous on a registered-walled tutorial page → 403 + sign-in card +
  signup CTA, both with ``?next=<page url>``.
* Free user below tier on a paid-tier tutorial page → 403 + tier badge
  + ``View Pricing`` CTA, no signup CTA.
* Eligible user → 200 + full body, no fade.
* Empty page body → bare paywall card (no teaser fade).
* Unverified-email user on a registered-walled page → 200 + verify card.
* Anonymous on a registered-walled video page → 403 + locked thumbnail
  + sign-in card.
* Free user below recording tier → 403 + ``View Pricing`` + tier badge.
* Eligible user on video page → 200 + embed.
* Workshops list / detail "Free" badge extends to ``LEVEL_REGISTERED``.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_REGISTERED,
)
from content.models import (
    Instructor,
    Workshop,
    WorkshopInstructor,
    WorkshopPage,
)
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _attach_instructor(workshop, name='Alice'):
    instructor, _ = Instructor.objects.get_or_create(
        name=name,
        defaults={
            'instructor_id': name.lower().replace(' ', '-'),
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.create(
        workshop=workshop, instructor=instructor, position=0,
    )
    return instructor


def _make_workshop(slug, *, pages=LEVEL_REGISTERED, recording=None,
                   landing=LEVEL_OPEN, with_event=True, description='',
                   recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                   timestamps=None):
    """Create a workshop for teaser tests.

    ``recording`` defaults to ``LEVEL_BASIC`` so the gate-ordering
    invariant ``pages <= recording`` is satisfied with the default
    ``pages == LEVEL_REGISTERED``. Passing an explicit value sets the
    recording gate directly. ``recording_required_level`` keeps the
    legacy ``VISIBILITY_CHOICES`` per the issue spec, so we don't pass
    ``LEVEL_REGISTERED`` here.
    """
    if recording is None:
        # LEVEL_REGISTERED (5) > LEVEL_OPEN (0) and < LEVEL_BASIC (10),
        # so the recording gate must be at least LEVEL_BASIC to satisfy
        # the workshop's recording >= pages invariant when pages is
        # LEVEL_REGISTERED.
        recording = LEVEL_BASIC if pages >= LEVEL_REGISTERED else LEVEL_OPEN
    event = None
    if with_event:
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=f'Event {slug}',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            recording_url=recording_url,
            timestamps=timestamps or [],
            published=True,
        )
    workshop = Workshop.objects.create(
        slug=slug,
        title=f'Workshop {slug}',
        status='published',
        date=date(2026, 4, 21),
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
        event=event,
    )
    _attach_instructor(workshop)
    return workshop


# Body long enough to exceed the 150-word teaser budget. Mixes paragraphs
# so the truncator's tag-balancing path is exercised.
_LONG_BODY = (
    '# Tutorial intro\n\n'
    'This first paragraph contains the unique phrase WORKSHOPTEASERMARKER '
    'which we expect to render in the teaser. '
    + '\n\n'.join(
        f'Paragraph {i} sentence one with several distinct teaser words. '
        f'Paragraph {i} sentence two adds more recognisable words.'
        for i in range(8)
    )
    + '\n\nWORKSHOPHIDDENMARKER which should NOT appear in the teaser '
    'because it sits well past the 150-word cutoff.'
)

_LONG_DESCRIPTION = (
    'This recording walks you through the WORKSHOPVIDEOMARKER pipeline '
    'in depth.\n\n'
    + '\n\n'.join(
        'Paragraph %d discusses architecture, failure modes, deployment '
        'trade-offs, and operational practices that turn a prototype '
        'into a production system. Many small choices compound here.'
        % i for i in range(8)
    )
    + '\n\nWORKSHOPVIDEOHIDDEN past the 150-word fade-out cutoff.'
)


# ---------------------------------------------------------------------
# Model-level tests
# ---------------------------------------------------------------------


class PagesRequiredLevelChoicesTest(TierSetupMixin, TestCase):
    """LEVEL_REGISTERED is now a valid choice for pages_required_level."""

    def test_level_registered_accepted(self):
        ws = Workshop.objects.create(
            slug='reg-ws', title='Registered Workshop',
            status='published',
            date=date(2026, 4, 21),
            landing_required_level=LEVEL_OPEN,
            pages_required_level=LEVEL_REGISTERED,
            recording_required_level=LEVEL_BASIC,
        )
        self.assertEqual(ws.pages_required_level, LEVEL_REGISTERED)
        # No ValidationError on save (the gate-ordering invariant is
        # satisfied: 0 <= 5 <= 10) and the choices validator now
        # accepts LEVEL_REGISTERED on pages_required_level.
        ws.full_clean()


class UserCanAccessPagesRegisteredTest(TierSetupMixin, TestCase):
    """user_can_access_pages routes the registration wall correctly."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'access-reg', pages=LEVEL_REGISTERED, recording=LEVEL_REGISTERED,
        )

    def test_anonymous_blocked(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(self.workshop.user_can_access_pages(AnonymousUser()))

    def test_free_verified_user_allowed(self):
        user = User.objects.create_user(
            email='reg-free@x.com', password='pw',
            tier=self.free_tier, email_verified=True,
        )
        self.assertTrue(self.workshop.user_can_access_pages(user))

    def test_free_unverified_user_blocked(self):
        user = User.objects.create_user(
            email='reg-unv@x.com', password='pw',
            tier=self.free_tier, email_verified=False,
        )
        self.assertFalse(self.workshop.user_can_access_pages(user))

    def test_basic_paid_user_allowed_even_unverified(self):
        # Mirror can_access semantics: paid tiers bypass the verify gate
        # because their billing is the verification.
        user = User.objects.create_user(
            email='reg-basic@x.com', password='pw',
            tier=self.basic_tier, email_verified=False,
        )
        self.assertTrue(self.workshop.user_can_access_pages(user))


# ---------------------------------------------------------------------
# Workshop tutorial page (gated branches)
# ---------------------------------------------------------------------


class RegisteredTutorialAnonymousTest(TierSetupMixin, TestCase):
    """Anonymous visitor on a free-with-registration tutorial."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'reg-tut', pages=LEVEL_REGISTERED, recording=LEVEL_REGISTERED,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='intro', title='Intro',
            sort_order=1, body=_LONG_BODY,
        )
        cls.url = '/workshops/reg-tut/tutorial/intro'

    def test_returns_403(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_renders_title_and_breadcrumb(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'data-testid="page-title"', status_code=403,
        )
        self.assertContains(
            response, 'data-testid="page-breadcrumb"', status_code=403,
        )

    def test_renders_teaser_body(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'data-testid="teaser-body"', status_code=403,
        )
        self.assertContains(
            response, 'WORKSHOPTEASERMARKER', status_code=403,
        )

    def test_omits_late_body_content(self):
        response = self.client.get(self.url)
        self.assertNotContains(
            response, 'WORKSHOPHIDDENMARKER', status_code=403,
        )

    def test_renders_signin_and_signup_buttons(self):
        response = self.client.get(self.url)
        # Sign-in (primary) link with next= preserved.
        self.assertContains(
            response,
            'href="/accounts/login/?next=%2Fworkshops%2Freg-tut%2Ftutorial%2Fintro"',
            status_code=403,
        )
        self.assertContains(response, 'Sign In', status_code=403)
        # Create-a-free-account (secondary) link with next= preserved.
        self.assertContains(
            response,
            'href="/accounts/signup/?next=%2Fworkshops%2Freg-tut%2Ftutorial%2Fintro"',
            status_code=403,
        )
        self.assertContains(
            response, 'Create a free account', status_code=403,
        )
        # Tier badge does NOT render — registered wall has no required tier.
        self.assertNotContains(
            response, 'data-testid="gated-required-tier"', status_code=403,
        )

    def test_renders_free_badge(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'data-testid="page-free-badge"', status_code=403,
        )

    def test_renders_locked_video_thumbnail(self):
        # Page has no video_start so no locked thumbnail (the video
        # context is anchored per page, not workshop-wide).
        response = self.client.get(self.url)
        self.assertNotContains(
            response, 'data-testid="teaser-video-thumbnail"',
            status_code=403,
        )


class FreeUserOnPaidTierTutorialTest(TierSetupMixin, TestCase):
    """Free signed-in user on a Basic-gated tutorial page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'paid-tut', pages=LEVEL_BASIC, recording=LEVEL_MAIN,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='lesson', title='Lesson',
            sort_order=1, body=_LONG_BODY,
        )
        cls.url = '/workshops/paid-tut/tutorial/lesson'
        # Issue #532: the test user is read-only — no test mutates it.
        cls.user = User.objects.create_user(
            email='free-paid@x.com', password='pw',
            tier=cls.free_tier, email_verified=True,
        )

    def setUp(self):
        self.client.login(email='free-paid@x.com', password='pw')

    def test_returns_403(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_renders_teaser_body_and_fade_wrapper(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'WORKSHOPTEASERMARKER', status_code=403,
        )
        self.assertContains(
            response, 'data-testid="teaser-body-wrapper"', status_code=403,
        )

    def test_renders_tier_badge(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'data-testid="gated-required-tier"', status_code=403,
        )
        self.assertContains(
            response, 'Basic or above required', status_code=403,
        )

    def test_renders_view_pricing_cta_only(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'View Pricing', status_code=403,
        )
        # Signed-in users do NOT get a "Create a free account" companion.
        self.assertNotContains(
            response, 'data-testid="teaser-signup-cta"', status_code=403,
        )

    def test_includes_current_access_state(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'Current access: Free member', status_code=403,
        )


class EligibleUserTutorialTest(TierSetupMixin, TestCase):
    """Main user passes the Basic gate and reads the full body."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'eligible-tut', pages=LEVEL_BASIC, recording=LEVEL_MAIN,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='lesson', title='Lesson',
            sort_order=1, body=_LONG_BODY,
        )
        cls.url = '/workshops/eligible-tut/tutorial/lesson'
        # Issue #532: read-only test user.
        cls.user = User.objects.create_user(
            email='main-eligible@x.com', password='pw',
            tier=cls.main_tier, email_verified=True,
        )

    def setUp(self):
        self.client.login(email='main-eligible@x.com', password='pw')

    def test_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_renders_full_body(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="page-body"')
        self.assertContains(response, 'WORKSHOPHIDDENMARKER')

    def test_no_teaser_or_fade(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, 'data-testid="teaser-body"')
        self.assertNotContains(response, 'data-testid="page-paywall"')


class EmptyBodyTutorialFallbackTest(TierSetupMixin, TestCase):
    """Empty body falls back to the bare paywall — no awkward empty fade."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'empty-body', pages=LEVEL_BASIC, recording=LEVEL_MAIN,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='empty', title='Empty Page',
            sort_order=1, body='',
        )
        cls.url = '/workshops/empty-body/tutorial/empty'
        # Issue #532: read-only test user.
        cls.user = User.objects.create_user(
            email='empty-body-free@x.com', password='pw',
            tier=cls.free_tier, email_verified=True,
        )

    def setUp(self):
        self.client.login(email='empty-body-free@x.com', password='pw')

    def test_returns_403(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_no_teaser_body(self):
        response = self.client.get(self.url)
        self.assertNotContains(
            response, 'data-testid="teaser-body"', status_code=403,
        )
        self.assertNotContains(
            response, 'data-testid="teaser-body-wrapper"', status_code=403,
        )

    def test_renders_paywall_card(self):
        response = self.client.get(self.url)
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertContains(response, 'View Pricing', status_code=403)


class UnverifiedEmailTutorialTest(TierSetupMixin, TestCase):
    """Unverified free user hits the verify-email card with status 200."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'verify-tut', pages=LEVEL_REGISTERED, recording=LEVEL_REGISTERED,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='intro', title='Intro',
            sort_order=1, body=_LONG_BODY,
        )

    def test_unverified_user_sees_verify_card(self):
        User.objects.create_user(
            email='unverified@x.com', password='pw',
            tier=self.free_tier, email_verified=False,
        )
        self.client.login(email='unverified@x.com', password='pw')
        response = self.client.get('/workshops/verify-tut/tutorial/intro')
        # Verify-email path returns 200 (the user can resolve it without
        # leaving the page).
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="verify-email-required-card"',
        )
        # Sign-in / fade UI are absent — this is a separate render path.
        self.assertNotContains(response, 'data-testid="teaser-body"')
        self.assertNotContains(response, 'data-testid="page-paywall"')


class TutorialWithVideoStartShowsThumbnailTest(TierSetupMixin, TestCase):
    """Pages anchored to the recording show a locked-video thumbnail."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'thumb-tut', pages=LEVEL_REGISTERED, recording=LEVEL_REGISTERED,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='step', title='Step',
            sort_order=1, body=_LONG_BODY, video_start='02:30',
        )

    def test_anonymous_sees_locked_thumbnail(self):
        response = self.client.get('/workshops/thumb-tut/tutorial/step')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="teaser-video-thumbnail"',
            status_code=403,
        )
        # YouTube hqdefault for the workshop's recording_url.
        self.assertContains(
            response,
            'img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
            status_code=403,
        )


# ---------------------------------------------------------------------
# Workshop video page (gated branches)
# ---------------------------------------------------------------------


class LegacyVideoRouteRedirectTest(TierSetupMixin, TestCase):
    """Issue #618: the standalone /video route is retired.

    The legacy URL 301-redirects to the unified course-player layout at
    ``/workshops/<slug>``. Any user (anon, free, paid) gets the same
    redirect — locked-vs-unlocked behaviour lives entirely in the new
    layout. The tests below assert the redirect contract and that the
    new player layout serves locked / unlocked variants correctly.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'reg-vid',
            pages=LEVEL_REGISTERED, recording=LEVEL_BASIC,
            description=_LONG_DESCRIPTION,
            timestamps=[
                {'time_seconds': 0, 'label': 'Intro'},
                {'time_seconds': 60, 'label': 'Setup'},
                {'time_seconds': 120, 'label': 'Demo'},
                {'time_seconds': 180, 'label': 'Q&A'},
            ],
        )

    def test_anonymous_video_route_redirects(self):
        response = self.client.get('/workshops/reg-vid/video')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/workshops/reg-vid')

    def test_video_route_preserves_t_param_on_redirect(self):
        response = self.client.get('/workshops/reg-vid/video?t=120')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/workshops/reg-vid?t=120')


class LockedRecordingPlayerLayoutTest(TierSetupMixin, TestCase):
    """Anon on a Basic-gated recording: outline renders as syllabus,
    no iframe markup, single discreet header link, no per-section noise."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'reg-vid',
            pages=LEVEL_REGISTERED, recording=LEVEL_BASIC,
            description=_LONG_DESCRIPTION,
            timestamps=[
                {'time_seconds': 0, 'label': 'Intro'},
                {'time_seconds': 60, 'label': 'Setup'},
                {'time_seconds': 120, 'label': 'Demo'},
                {'time_seconds': 180, 'label': 'Q&A'},
            ],
        )

    def test_no_iframe_markup_present(self):
        response = self.client.get('/workshops/reg-vid')
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertNotContains(response, 'loom.com/embed')
        self.assertNotContains(response, 'player.vimeo.com')

    def test_no_player_script_tag(self):
        response = self.client.get('/workshops/reg-vid')
        self.assertNotContains(response, 'workshop_player.js')
        self.assertNotContains(
            response, 'data-testid="workshop-player-script"',
        )

    def test_recording_outline_renders_for_anon(self):
        response = self.client.get('/workshops/reg-vid')
        self.assertContains(
            response, 'data-testid="workshop-outline-recording"',
        )
        self.assertContains(response, 'Intro')
        self.assertContains(response, 'Setup')
        # Locked chapter rows are inert (no <button>, just <div>).
        self.assertContains(
            response, 'data-testid="workshop-chapter-row-locked"',
        )

    def test_locked_header_link_present(self):
        response = self.client.get('/workshops/reg-vid')
        self.assertContains(
            response, 'data-testid="workshop-recording-locked-header-link"',
        )
        self.assertContains(response, 'Recording')
        self.assertContains(response, 'Get Basic')


class FreeUserOnPaidRecordingPlayerLayoutTest(TierSetupMixin, TestCase):
    """Free user on a Main-gated recording: locked-variant header link,
    no iframe markup."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'paid-vid',
            pages=LEVEL_BASIC, recording=LEVEL_MAIN,
            description=_LONG_DESCRIPTION,
        )
        cls.user = User.objects.create_user(
            email='free-vid@x.com', password='pw',
            tier=cls.free_tier, email_verified=True,
        )

    def setUp(self):
        self.client.login(email='free-vid@x.com', password='pw')

    def test_player_layout_renders_with_locked_header_link(self):
        response = self.client.get('/workshops/paid-vid')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="workshop-recording-locked-header-link"',
        )
        self.assertContains(response, 'Get Main')

    def test_no_iframe_markup_present(self):
        response = self.client.get('/workshops/paid-vid')
        self.assertNotContains(response, 'youtube.com/embed')
        self.assertNotContains(response, 'loom.com/embed')


class EligiblePlayerLayoutTest(TierSetupMixin, TestCase):
    """Premium user gets the player pane, the JS module, and no locked
    header link."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'elig-vid',
            pages=LEVEL_BASIC, recording=LEVEL_MAIN,
            description=_LONG_DESCRIPTION,
        )
        cls.user = User.objects.create_user(
            email='premium@x.com', password='pw',
            tier=cls.premium_tier, email_verified=True,
        )

    def setUp(self):
        self.client.login(email='premium@x.com', password='pw')

    def test_returns_200(self):
        response = self.client.get('/workshops/elig-vid')
        self.assertEqual(response.status_code, 200)

    def test_player_pane_renders(self):
        response = self.client.get('/workshops/elig-vid')
        self.assertContains(response, 'data-testid="workshop-player-pane"')

    def test_no_locked_header_link(self):
        response = self.client.get('/workshops/elig-vid')
        self.assertNotContains(
            response, 'data-testid="workshop-recording-locked-header-link"',
        )

    def test_player_script_tag_present(self):
        response = self.client.get('/workshops/elig-vid')
        self.assertContains(
            response, 'data-testid="workshop-player-script"',
        )


class PlayerLayoutMissingRecordingTest(TierSetupMixin, TestCase):
    """Workshop with no recording attached: no player pane, no recording
    outline, no locked header link."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            'no-rec', pages=LEVEL_BASIC, recording=LEVEL_MAIN,
            with_event=False, description=_LONG_DESCRIPTION,
        )

    def test_no_player_pane_when_workshop_lacks_recording(self):
        response = self.client.get('/workshops/no-rec')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'data-testid="workshop-player-pane"',
        )
        self.assertNotContains(
            response, 'data-testid="workshop-outline-recording"',
        )
        # No locked header link either — there's no recording to gate.
        self.assertNotContains(
            response, 'data-testid="workshop-recording-locked-header-link"',
        )


# ---------------------------------------------------------------------
# Catalog & landing badges (extension to LEVEL_REGISTERED)
# ---------------------------------------------------------------------


class FreeBadgeOnRegisteredWorkshopsTest(TierSetupMixin, TestCase):
    """Workshops list / detail render the Free badge for LEVEL_REGISTERED."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.open_ws = _make_workshop(
            'open-ws', pages=LEVEL_OPEN, recording=LEVEL_OPEN,
        )
        cls.reg_ws = _make_workshop(
            'reg-ws', pages=LEVEL_REGISTERED, recording=LEVEL_REGISTERED,
        )
        cls.paid_ws = _make_workshop(
            'paid-ws', pages=LEVEL_BASIC, recording=LEVEL_MAIN,
        )

    def test_catalog_renders_free_badge_for_registered_workshop(self):
        response = self.client.get('/workshops')
        # Two workshops below LEVEL_BASIC → two free badges.
        self.assertContains(
            response, 'data-testid="workshop-free-badge"', count=2,
        )
        # Paid workshop still shows the tier badge.
        self.assertContains(
            response, 'data-testid="workshop-tier-badge"', count=1,
        )

    def test_landing_renders_free_badge_for_registered_workshop(self):
        response = self.client.get('/workshops/reg-ws')
        self.assertContains(response, 'data-testid="workshop-free-badge"')
        self.assertNotContains(response, 'data-testid="workshop-tier-badge"')

    def test_landing_paid_workshop_keeps_tier_badge(self):
        response = self.client.get('/workshops/paid-ws')
        self.assertContains(response, 'data-testid="workshop-tier-badge"')
        self.assertNotContains(response, 'data-testid="workshop-free-badge"')
