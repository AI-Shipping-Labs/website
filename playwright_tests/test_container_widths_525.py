"""Issue #525 — every public page renders at the standardized container
width for its page-group, and never produces horizontal overflow on mobile.

The audit table in the issue defines three target groups:
    - marketing/listings    -> max-w-7xl
    - detail pages          -> max-w-5xl
    - reader / long-form    -> max-w-3xl

This test parametrizes the audited URLs and asserts:
1. The first ``mx-auto max-w-*`` wrapper inside ``<main>`` carries the
   target ``max-w-*`` class, at desktop viewport ``1280x900``.
2. The visible width of that wrapper is at most the target value plus
   2 * ``px-8`` (= 64 px) — i.e. content is actually constrained.
3. At mobile viewport ``390x844`` (Pixel 7), the page does not overflow:
   ``document.documentElement.scrollWidth <= window.innerWidth``.

If a future PR widens or narrows any audited template, the parametrized
assertion in this file fails and CI catches the drift — see issue #525
"PM declined to extract a shared partial — Playwright assertion catches
future drift".

Usage:
    uv run pytest playwright_tests/test_container_widths_525.py -v
"""

import datetime
import os
import uuid

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_site_config_tiers as _ensure_site_config_tiers,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# ---------------------------------------------------------------------------
# Target widths (px) for each Tailwind class. These are the canonical
# Tailwind defaults; if Tailwind is upgraded these need to be revisited.
# ---------------------------------------------------------------------------
TARGET_WIDTHS_PX = {
    'max-w-3xl': 768,
    'max-w-5xl': 1024,
    'max-w-7xl': 1280,
}

# Account for the standard horizontal padding budget: ``px-4 sm:px-6 lg:px-8``
# = 32 px each side on lg viewport. ``clientWidth`` includes padding, so
# the visible frame is at most ``max-w + 2 * 32 = max-w + 64``.
PADDING_BUDGET_PX = 64

DESKTOP_VIEWPORT = {'width': 1280, 'height': 900}
MOBILE_VIEWPORT = {'width': 390, 'height': 844}


# ---------------------------------------------------------------------------
# Test fixtures (ORM helpers)
# ---------------------------------------------------------------------------


def _seed_listings():
    """Seed minimal data so listing pages have content (not just empty
    states) and the outermost wrapper renders normally. Returns a dict
    of slugs/uuids the parametrized tests interpolate into URL paths.
    """
    from content.models import (
        Article,
        Course,
        CuratedLink,
        Download,
        InterviewCategory,
        Module,
        Project,
        Tutorial,
        Workshop,
    )
    from events.models import Event
    from notifications.models import Notification
    from voting.models import Poll, PollOption

    # Wipe existing rows in the relevant tables to ensure tests are
    # deterministic regardless of order.
    Article.objects.all().delete()
    Course.objects.all().delete()
    Project.objects.all().delete()
    Tutorial.objects.all().delete()
    Download.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Poll.objects.all().delete()
    Notification.objects.all().delete()
    CuratedLink.objects.all().delete()
    InterviewCategory.objects.all().delete()

    Article.objects.create(
        title='Sample Article',
        slug='sample-article',
        description='Sample description.',
        content_markdown='# Sample Article\n\nBody content.',
        author='Author',
        tags=['mlops'],
        published=True,
        date=datetime.date(2026, 1, 1),
    )

    course = Course.objects.create(
        title='Sample Course',
        slug='sample-course',
        description='Sample course.',
        status='published',
    )
    Module.objects.create(
        course=course,
        title='Sample Module',
        slug='sample-module',
        sort_order=1,
        overview='# Sample Module\n\nModule overview body.',
    )

    Project.objects.create(
        title='Sample Project',
        slug='sample-project',
        description='Sample project.',
        content_markdown='# Sample Project',
        published=True,
        date=datetime.date(2026, 1, 1),
    )

    Tutorial.objects.create(
        title='Sample Tutorial',
        slug='sample-tutorial',
        description='Sample tutorial.',
        content_markdown='# Sample Tutorial',
        published=True,
        date=datetime.date(2026, 1, 1),
    )

    Download.objects.create(
        title='Sample Download',
        slug='sample-download',
        description='Sample download.',
        file_url='https://example.com/file.pdf',
        file_type='pdf',
        published=True,
    )

    Workshop.objects.create(
        title='Sample Workshop',
        slug='sample-workshop',
        description='Sample workshop.',
        date=datetime.date(2026, 1, 1),
        status='published',
    )

    start_dt = timezone.now() + datetime.timedelta(days=7)
    Event.objects.create(
        title='Sample Event',
        slug='sample-event',
        description='Sample event.',
        published=True,
        start_datetime=start_dt,
        status='upcoming',
    )

    poll = Poll.objects.create(
        title='Sample Poll',
        description='Vote on something.',
        status='open',
    )
    PollOption.objects.create(poll=poll, title='Option A')
    PollOption.objects.create(poll=poll, title='Option B')

    # Curated link for /resources
    CuratedLink.objects.create(
        item_id='sample-tool',
        title='Sample Tool',
        description='Sample tool.',
        url='https://example.com/tool',
        category='tools',
        sort_order=1,
        published=True,
    )

    # Interview category so /interview renders (the hub view 404s
    # when no categories exist).
    InterviewCategory.objects.create(
        slug='theory',
        title='Theory Questions',
        description='Theory interview questions.',
        status='published',
        body_markdown='# Theory Questions',
    )

    connection.close()
    return {
        'article_slug': 'sample-article',
        'course_slug': 'sample-course',
        'module_slug': 'sample-module',
        'project_slug': 'sample-project',
        'tutorial_slug': 'sample-tutorial',
        'workshop_slug': 'sample-workshop',
        'event_slug': 'sample-event',
        'poll_uuid': str(poll.id),
    }


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------


def _outer_wrapper_class_string(page):
    """Return the class attribute of the first ``mx-auto max-w-*`` div
    inside ``<main>``. This is the standardized "outer page-frame
    wrapper" the audit table targets.
    """
    return page.evaluate(
        """() => {
            const main = document.querySelector('main');
            if (!main) return null;
            // Walk into main looking for the first div whose class
            // string contains both ``mx-auto`` and ``max-w-``.
            const candidates = main.querySelectorAll('div');
            for (const el of candidates) {
                const cls = el.className || '';
                if (typeof cls === 'string' &&
                    cls.includes('mx-auto') &&
                    /max-w-(3xl|4xl|5xl|6xl|7xl|2xl|md|lg)/.test(cls)) {
                    return cls;
                }
            }
            return null;
        }"""
    )


def _outer_wrapper_client_width(page):
    """Return the ``clientWidth`` (px) of the outer page-frame wrapper."""
    return page.evaluate(
        """() => {
            const main = document.querySelector('main');
            if (!main) return null;
            const candidates = main.querySelectorAll('div');
            for (const el of candidates) {
                const cls = el.className || '';
                if (typeof cls === 'string' &&
                    cls.includes('mx-auto') &&
                    /max-w-(3xl|4xl|5xl|6xl|7xl|2xl|md|lg)/.test(cls)) {
                    return el.clientWidth;
                }
            }
            return null;
        }"""
    )


def _has_horizontal_overflow(page):
    """True if the page produces a horizontal scrollbar at the current
    viewport.
    """
    return page.evaluate(
        'document.documentElement.scrollWidth > window.innerWidth'
    )


# ---------------------------------------------------------------------------
# Parametrized desktop assertions
# ---------------------------------------------------------------------------

# Each tuple: (path-template, expected-max-w-class, login-email-or-none)
# - path-template uses ``{slug}`` placeholders that ``_seed_listings``
#   provides via the returned dict.
# - login email of None means anonymous; otherwise the ``page`` is
#   replaced with an authed context.
LISTINGS_WIDE = [
    ('/', 'max-w-7xl', None),
    ('/blog', 'max-w-7xl', None),
    ('/courses', 'max-w-7xl', None),
    ('/projects', 'max-w-7xl', None),
    ('/tutorials', 'max-w-7xl', None),
    ('/downloads', 'max-w-7xl', None),
    ('/workshops', 'max-w-7xl', None),
    ('/events', 'max-w-7xl', None),
    ('/events/calendar', 'max-w-7xl', None),
    ('/vote', 'max-w-7xl', None),
    ('/tags', 'max-w-7xl', None),
    ('/interview', 'max-w-7xl', None),
    ('/resources', 'max-w-7xl', None),
    ('/pricing', 'max-w-7xl', None),
    ('/activities', 'max-w-7xl', None),
]

DETAIL_MEDIUM = [
    ('/courses/{course_slug}', 'max-w-5xl'),
    ('/courses/{course_slug}/{module_slug}', 'max-w-5xl'),
    # Issue #618: the workshop landing now hosts the two-pane course-player
    # layout (20rem outline + tutorial body) and uses ``max-w-7xl`` so the
    # player has room to breathe. The detail-page parametrization keeps it
    # listed (with the wider frame) so a future regression is still caught.
    ('/workshops/{workshop_slug}', 'max-w-7xl'),
    ('/events/{event_slug}', 'max-w-5xl'),
    ('/vote/{poll_uuid}', 'max-w-5xl'),
]

READER_NARROW = [
    ('/blog/{article_slug}', 'max-w-3xl'),
    ('/tutorials/{tutorial_slug}', 'max-w-3xl'),
    ('/projects/{project_slug}', 'max-w-3xl'),
    ('/about', 'max-w-3xl'),
    ('/terms', 'max-w-3xl'),
    ('/privacy', 'max-w-3xl'),
    ('/impressum', 'max-w-3xl'),
]


@pytest.mark.django_db(transaction=True)
class TestListingPagesUseMaxW7xl:
    """Marketing / listing pages all share ``max-w-7xl`` so the page
    frame doesn't visibly jump when the user clicks between them.
    """

    @pytest.mark.parametrize('path,expected_max_w,_email', LISTINGS_WIDE)
    def test_outer_wrapper_has_target_max_width(
        self, django_server, browser, path, expected_max_w, _email,
    ):
        _ensure_tiers()
        _ensure_site_config_tiers()
        _seed_listings()

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            page.goto(f'{django_server}{path}', wait_until='domcontentloaded')

            cls = _outer_wrapper_class_string(page)
            assert cls is not None, (
                f'No mx-auto max-w-* wrapper found inside <main> on {path}'
            )
            assert expected_max_w in cls, (
                f'{path}: expected outer wrapper to carry {expected_max_w}, '
                f'got class string: {cls!r}'
            )

            width = _outer_wrapper_client_width(page)
            target_px = TARGET_WIDTHS_PX[expected_max_w]
            assert width <= target_px + PADDING_BUDGET_PX, (
                f'{path}: outer wrapper clientWidth={width}px exceeds '
                f'{target_px}+{PADDING_BUDGET_PX}px budget for '
                f'{expected_max_w}'
            )
        finally:
            ctx.close()


@pytest.mark.django_db(transaction=True)
class TestDetailPagesUseMaxW5xl:
    """Detail pages share ``max-w-5xl`` — wider than reader, narrower
    than listing.
    """

    @pytest.mark.parametrize('path_tpl,expected_max_w', DETAIL_MEDIUM)
    def test_outer_wrapper_has_target_max_width(
        self, django_server, browser, path_tpl, expected_max_w,
    ):
        _ensure_tiers()
        _ensure_site_config_tiers()
        slugs = _seed_listings()
        path = path_tpl.format(**slugs)

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            page.goto(f'{django_server}{path}', wait_until='domcontentloaded')

            cls = _outer_wrapper_class_string(page)
            assert cls is not None, (
                f'No mx-auto max-w-* wrapper found inside <main> on {path}'
            )
            assert expected_max_w in cls, (
                f'{path}: expected outer wrapper to carry {expected_max_w}, '
                f'got class string: {cls!r}'
            )

            width = _outer_wrapper_client_width(page)
            target_px = TARGET_WIDTHS_PX[expected_max_w]
            assert width <= target_px + PADDING_BUDGET_PX, (
                f'{path}: outer wrapper clientWidth={width}px exceeds '
                f'{target_px}+{PADDING_BUDGET_PX}px budget for '
                f'{expected_max_w}'
            )
        finally:
            ctx.close()


@pytest.mark.django_db(transaction=True)
class TestReaderPagesUseMaxW3xl:
    """Reader / long-form pages share ``max-w-3xl`` so prose wraps at
    ~65–75 chars per line.
    """

    @pytest.mark.parametrize('path_tpl,expected_max_w', READER_NARROW)
    def test_outer_wrapper_has_target_max_width(
        self, django_server, browser, path_tpl, expected_max_w,
    ):
        _ensure_tiers()
        _ensure_site_config_tiers()
        slugs = _seed_listings()
        path = path_tpl.format(**slugs)

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            page.goto(f'{django_server}{path}', wait_until='domcontentloaded')

            cls = _outer_wrapper_class_string(page)
            assert cls is not None, (
                f'No mx-auto max-w-* wrapper found inside <main> on {path}'
            )
            assert expected_max_w in cls, (
                f'{path}: expected outer wrapper to carry {expected_max_w}, '
                f'got class string: {cls!r}'
            )

            width = _outer_wrapper_client_width(page)
            target_px = TARGET_WIDTHS_PX[expected_max_w]
            assert width <= target_px + PADDING_BUDGET_PX, (
                f'{path}: outer wrapper clientWidth={width}px exceeds '
                f'{target_px}+{PADDING_BUDGET_PX}px budget for '
                f'{expected_max_w}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Mobile: no horizontal overflow on any audited public page
# ---------------------------------------------------------------------------

# A representative sample — listings, detail, and reader — at 390x844.
# We do not exercise every URL on mobile because that would balloon test
# time; the desktop suite already covers per-page width assertions, and
# a mismatch between desktop frame width and mobile padding would only
# manifest on a handful of pages with non-standard padding.
MOBILE_PATHS = [
    '/',
    '/blog',
    '/courses',
    '/resources',
    '/events',
    '/projects',
    '/workshops',
    '/pricing',
    '/about',
    '/terms',
    '/blog/{article_slug}',
    '/courses/{course_slug}',
]


@pytest.mark.django_db(transaction=True)
class TestMobileNoHorizontalOverflow:
    """At 390x844 (Pixel 7), no public page produces a horizontal
    scrollbar — i.e. ``px-4`` is in effect and inner content respects
    the frame.
    """

    @pytest.mark.parametrize('path_tpl', MOBILE_PATHS)
    def test_no_overflow_on_mobile(
        self, django_server, browser, path_tpl,
    ):
        _ensure_tiers()
        _ensure_site_config_tiers()
        slugs = _seed_listings()
        path = path_tpl.format(**slugs)

        ctx = browser.new_context(viewport=MOBILE_VIEWPORT)
        page = ctx.new_page()
        try:
            page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
            assert not _has_horizontal_overflow(page), (
                f'{path} produces horizontal overflow at '
                f'{MOBILE_VIEWPORT["width"]}x{MOBILE_VIEWPORT["height"]}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Authenticated dashboard / account
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAuthenticatedDashboardAndAccount:
    """The authenticated homepage (dashboard) is ``max-w-7xl`` and the
    account page is ``max-w-5xl`` — verifies the two sibling shells
    share the standard widths.
    """

    def test_dashboard_uses_wide_frame(self, django_server, browser):
        _ensure_tiers()
        _ensure_site_config_tiers()
        _seed_listings()
        email = f'main-{uuid.uuid4().hex[:8]}@test.com'
        _create_user(email, tier_slug='main')

        ctx = _auth_context(browser, email)
        # _auth_context always uses the shared VIEWPORT; replace by
        # creating a fresh page-level viewport setting where we need it.
        ctx.set_default_navigation_timeout(15000)
        page = ctx.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        try:
            page.goto(f'{django_server}/', wait_until='domcontentloaded')
            cls = _outer_wrapper_class_string(page)
            assert cls is not None
            assert 'max-w-7xl' in cls, (
                f'authenticated / dashboard: expected max-w-7xl, got: {cls!r}'
            )
        finally:
            ctx.close()

    def test_account_page_uses_narrow_frame(self, django_server, browser):
        _ensure_tiers()
        _ensure_site_config_tiers()
        email = f'main-{uuid.uuid4().hex[:8]}@test.com'
        _create_user(email, tier_slug='main')

        ctx = _auth_context(browser, email)
        ctx.set_default_navigation_timeout(15000)
        page = ctx.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        try:
            page.goto(
                f'{django_server}/account/',
                wait_until='domcontentloaded',
            )
            cls = _outer_wrapper_class_string(page)
            assert cls is not None
            assert 'max-w-5xl' in cls, (
                f'/account/: expected max-w-5xl, got: {cls!r}'
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Cross-page consistency: the wide-frame width is identical between
# pages, so the user does not see the page chrome jump while clicking
# around.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestListingFrameWidthConsistency:
    """Navigating between marketing / listing pages produces the same
    outer container width (within ±1 px) — the user does not see the
    page frame jump in or out.
    """

    def test_listings_share_frame_width(self, django_server, browser):
        _ensure_tiers()
        _ensure_site_config_tiers()
        _seed_listings()

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            urls = [
                '/',
                '/blog',
                '/courses',
                '/resources',
                '/projects',
                '/events',
                '/workshops',
                '/tutorials',
                '/downloads',
                '/vote',
                '/tags',
            ]
            widths = []
            for u in urls:
                page.goto(
                    f'{django_server}{u}',
                    wait_until='domcontentloaded',
                )
                w = _outer_wrapper_client_width(page)
                assert w is not None, f'No outer wrapper on {u}'
                widths.append((u, w))

            min_w = min(w for _, w in widths)
            max_w = max(w for _, w in widths)
            assert max_w - min_w <= 1, (
                'Listing page frame widths differ by more than 1px: '
                f'{widths}'
            )
        finally:
            ctx.close()
