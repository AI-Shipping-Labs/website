"""Issue #525 — every public page renders at the standardized container
width for its page-group, and never produces horizontal overflow on mobile.

The audit table defines these target groups (updated by issues #1340/#1395):
    - wide grids / marketing / dashboard             -> max-w-7xl
    - calendar / catalogs / mixed-layout indexes     -> max-w-5xl
    - mixed-layout detail pages                      -> max-w-5xl
    - reader / long-form / editorial row feeds       -> max-w-3xl

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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

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
    from plans.models import Sprint
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
    Sprint.objects.all().delete()

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
    sample_event = Event.objects.create(
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
        item_id='sample-workshop',
        title='Sample Workshop Resource',
        description='Sample workshop resource.',
        url='https://example.com/tool',
        category='workshops',
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

    Sprint.objects.create(
        name='Sample Sprint',
        slug='sample-sprint',
        start_date=timezone.localdate(),
        duration_weeks=4,
        status='active',
        min_tier_level=20,
    )

    # Issue #673: canonical event URL is ``/events/<id>/<slug>``.
    event_path = sample_event.get_absolute_url()
    connection.close()
    return {
        'article_slug': 'sample-article',
        'course_slug': 'sample-course',
        'module_slug': 'sample-module',
        'project_slug': 'sample-project',
        'tutorial_slug': 'sample-tutorial',
        # Issue #915: workshop detail URLs are keyed on the canonical
        # ``<YYYY-MM-DD>-<slug>`` shape; bare-slug URLs no longer redirect.
        'workshop_slug': '2026-01-01-sample-workshop',
        'event_slug': 'sample-event',
        'event_path': event_path,
        'poll_uuid': str(poll.id),
        'sprint_slug': 'sample-sprint',
    }


def _seed_book_routes(email):
    """Seed every Book Club lifecycle/reader route for issue #1397."""
    from accounts.models import User
    from bookclub.models import Book, Chapter, ChapterRead

    Book.objects.all().delete()
    user = User.objects.get(email=email)
    current = Book.objects.create(
        title='Current reader-width book',
        slug='current-reader-width-book',
        author='Test Author',
        required_level=20,
        status='current',
        start_date=timezone.localdate(),
        summary='Published overall summary.',
        summary_published_at=timezone.now(),
    )
    chapter = Chapter.objects.create(
        book=current,
        number=0,
        title='Reader-width chapter',
        deadline=timezone.localdate() + datetime.timedelta(days=3),
        summary='Published chapter summary.',
        summary_published_at=timezone.now(),
    )
    ChapterRead.objects.create(user=user, chapter=chapter)
    upcoming = Book.objects.create(
        title='Upcoming reader-width book',
        slug='upcoming-reader-width-book',
        author='Test Author',
        required_level=20,
        status='upcoming',
        start_date=timezone.localdate() + datetime.timedelta(days=30),
    )
    finished = Book.objects.create(
        title='Finished reader-width book',
        slug='finished-reader-width-book',
        author='Test Author',
        required_level=20,
        status='finished',
        start_date=timezone.localdate() - datetime.timedelta(days=30),
        summary='Published finished-book summary.',
        summary_published_at=timezone.now(),
    )
    connection.close()
    return {
        'hub': '/books',
        'current': current.get_absolute_url(),
        'upcoming': upcoming.get_absolute_url(),
        'finished': finished.get_absolute_url(),
        'chapter': f'/books/{current.slug}/chapters/{chapter.number}',
        'progress': f'/books/{current.slug}/progress',
        'summary': f'/books/{current.slug}/summary',
        'profile': f'/books/{current.slug}/readers/{user.pk}',
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
# Genuine multi-column grids / marketing / dashboard keep the full Frame
# (max-w-7xl): they visibly consume the width.
LISTINGS_WIDE = [
    ('/', 'max-w-7xl', None),
    ('/courses', 'max-w-7xl', None),
    ('/projects', 'max-w-7xl', None),
    ('/resources', 'max-w-7xl', None),
    ('/membership', 'max-w-7xl', None),
    ('/activities', 'max-w-7xl', None),
]

# Single-column row feeds and sparse 2-column hubs re-tiered 7xl -> 5xl by the
# 2026-08-06 addendum (issue #1340): at 7xl their content filled only ~55-70%
# of the column and the right half read empty. Each now aligns with its own
# detail page. See _docs/audits/2026-07-21-container-widths.md -> "2026-08-06
# addendum". /workshops here is the 5xl landing-page hero; its standalone
# archive and the other text-first editorial feeds use Reader width below.
LISTINGS_NARROW = [
    ('/tutorials', 'max-w-5xl', None),
    ('/downloads', 'max-w-5xl', None),
    ('/workshops', 'max-w-5xl', None),
    ('/events/calendar', 'max-w-5xl', None),
    ('/vote', 'max-w-5xl', None),
    ('/tags', 'max-w-5xl', None),
]

DETAIL_MEDIUM = [
    ('/courses/{course_slug}', 'max-w-5xl'),
    ('/courses/{course_slug}/{module_slug}', 'max-w-5xl'),
    ('/vote/{poll_uuid}', 'max-w-5xl'),
]

READER_NARROW = [
    ('/sprints', 'max-w-3xl'),
    ('/sprints/{sprint_slug}', 'max-w-3xl'),
    ('/blog', 'max-w-3xl'),
    ('/blog/{article_slug}', 'max-w-3xl'),
    ('/interview', 'max-w-3xl'),
    ('/workshops/catalog', 'max-w-3xl'),
    ('/workshops/{workshop_slug}', 'max-w-3xl'),
    ('/events', 'max-w-3xl'),
    ('{event_path}', 'max-w-3xl'),
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
class TestListingPagesUseMaxW5xl:
    """Single-column row feeds and sparse 2-column hubs share ``max-w-5xl``
    so the sparse right half no longer reads as empty (issue #1340). Each
    also aligns with its own detail page.
    """

    @pytest.mark.parametrize('path,expected_max_w,_email', LISTINGS_NARROW)
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


BOOK_ROUTE_WIDTHS = [
    ('hub', 'max-w-5xl'),
    ('current', 'max-w-3xl'),
    ('upcoming', 'max-w-3xl'),
    ('finished', 'max-w-3xl'),
    ('chapter', 'max-w-3xl'),
    ('progress', 'max-w-3xl'),
    ('summary', 'max-w-3xl'),
    ('profile', 'max-w-3xl'),
]


@pytest.mark.django_db(transaction=True)
class TestBookClubHubAndOpenedBookWidths:
    """The hub is 5xl; every opened-book lifecycle surface is 3xl."""

    @pytest.mark.parametrize('route_key,expected_max_w', BOOK_ROUTE_WIDTHS)
    def test_rendered_book_route_uses_its_canonical_width(
        self, django_server, browser, route_key, expected_max_w,
    ):
        _ensure_tiers()
        email = f'book-width-{uuid.uuid4().hex[:8]}@test.com'
        _create_user(email, tier_slug='main')
        paths = _seed_book_routes(email)
        path = paths[route_key]

        ctx = _auth_context(browser, email)
        page = ctx.new_page()
        page.set_viewport_size(DESKTOP_VIEWPORT)
        try:
            response = page.goto(
                f'{django_server}{path}',
                wait_until='domcontentloaded',
            )
            assert response is not None
            assert response.status == 200
            cls = _outer_wrapper_class_string(page)
            assert cls is not None, f'No outer wrapper on {path}'
            assert expected_max_w in cls, (
                f'{path}: expected {expected_max_w}, got {cls!r}'
            )
            width = _outer_wrapper_client_width(page)
            assert width <= (
                TARGET_WIDTHS_PX[expected_max_w] + PADDING_BUDGET_PX
            )
        finally:
            ctx.close()

    def test_mobile_book_journey_has_no_horizontal_overflow(
        self, django_server, browser,
    ):
        _ensure_tiers()
        email = f'book-mobile-{uuid.uuid4().hex[:8]}@test.com'
        _create_user(email, tier_slug='main')
        paths = _seed_book_routes(email)

        ctx = _auth_context(browser, email)
        page = ctx.new_page()
        page.set_viewport_size(MOBILE_VIEWPORT)
        try:
            for route_key, _expected_max_w in BOOK_ROUTE_WIDTHS:
                path = paths[route_key]
                response = page.goto(
                    f'{django_server}{path}',
                    wait_until='domcontentloaded',
                )
                assert response is not None
                assert response.status == 200
                assert not _has_horizontal_overflow(page), (
                    f'{path} overflows at {MOBILE_VIEWPORT}'
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
    '/events/calendar',
    '/projects',
    '/workshops',
    '/membership',
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
    """Navigating within a width band produces the same outer container
    width (within ±1 px) — the user does not see the page frame jump in or
    out. The three bands are 7xl grids, 5xl catalogs/mixed indexes, and 3xl
    editorial feeds. A page in one band is not required to match a page in
    another, so each band is asserted separately.
    """

    def _measure(self, django_server, page, urls):
        widths = []
        for u in urls:
            page.goto(
                f'{django_server}{u}',
                wait_until='domcontentloaded',
            )
            w = _outer_wrapper_client_width(page)
            assert w is not None, f'No outer wrapper on {u}'
            widths.append((u, w))
        return widths

    def test_wide_grid_listings_share_frame_width(self, django_server, browser):
        _ensure_tiers()
        _ensure_site_config_tiers()
        _seed_listings()

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            widths = self._measure(
                django_server,
                page,
                ['/', '/courses', '/resources', '/projects'],
            )
            min_w = min(w for _, w in widths)
            max_w = max(w for _, w in widths)
            assert max_w - min_w <= 1, (
                'Wide (7xl) listing page frame widths differ by more than '
                f'1px: {widths}'
            )
        finally:
            ctx.close()

    def test_detail_listings_share_frame_width(self, django_server, browser):
        _ensure_tiers()
        _ensure_site_config_tiers()
        _seed_listings()

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            # /workshops measures its 5xl hero (first mx-auto max-w-* in main).
            widths = self._measure(
                django_server,
                page,
                [
                    '/workshops', '/events/calendar', '/tutorials',
                    '/downloads', '/vote', '/tags',
                ],
            )
            min_w = min(w for _, w in widths)
            max_w = max(w for _, w in widths)
            assert max_w - min_w <= 1, (
                'Detail (5xl) listing page frame widths differ by more than '
                f'1px: {widths}'
            )
        finally:
            ctx.close()

    def test_reader_feeds_share_frame_width(self, django_server, browser):
        _ensure_tiers()
        _ensure_site_config_tiers()
        _seed_listings()

        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        try:
            widths = self._measure(
                django_server,
                page,
                ['/blog', '/events', '/workshops/catalog', '/interview'],
            )
            min_w = min(w for _, w in widths)
            max_w = max(w for _, w in widths)
            assert max_w - min_w <= 1, (
                'Reader (3xl) feed frame widths differ by more than '
                f'1px: {widths}'
            )
        finally:
            ctx.close()
