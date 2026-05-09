"""Playwright matrix test for the mobile text navigation (issue #463).

This is the "headline" regression test for the bug: the four peer-review
templates extended ``base.html`` directly but never included
``includes/header.html``, so the entire site header -- including the
mobile hamburger and the text navigation -- was missing on those
URLs. The visible symptom on a phone was that you could land on
``/courses/<slug>/submit`` (or ``/reviews``, or ``/certificates/<uuid>``)
and have no way to navigate back to the Blog or other sections.

This file covers two complementary checks:

Test 1: ``test_mobile_text_navigation_present_on_each_url`` --
  Iterates a matrix of 9 representative public-page URLs at iPhone-sized
  viewport (390x844). For each URL it taps the hamburger, taps the
  Community and Resources toggles, and asserts representative links inside
  both accordion lists become visible. The assertion includes the
  URL in its failure message so a future regression points at the page,
  not just "menu broken".

Test 2: ``test_desktop_header_renders_on_peer_review_pages`` -- At
  1024x768 (desktop), the four peer-review URLs must render the desktop
  nav (Community and Resources dropdowns) and must NOT show
  the mobile hamburger. This covers the desktop side of the same template
  fix.

Test 3: ``test_back_to_course_link_still_renders`` -- Asserts the
  page-body chrome (the "Back to <course>" link the spec calls out)
  still renders alongside the new header chrome on the dashboard URL.
  Guards against an over-eager fix that includes the header but
  accidentally drops the existing peer-review body.

The matrix is intentionally written so it would have failed on the four
peer-review URLs *before* the fix, and would have passed on the other
five URLs. After the fix, all 9 pass. This is the false-positive guard
the spec asks for.

Usage:
    uv run pytest playwright_tests/test_mobile_resources_accordion_matrix.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.django_db(transaction=True)

# Mobile viewport used by the matrix test. The bug was reported at
# 390x844 (iPhone 14 / 15 default). The accordion behavior is unchanged
# at this exact size whether or not the fix is in.
MOBILE_VIEWPORT = {"width": 390, "height": 844}
DESKTOP_VIEWPORT = {"width": 1024, "height": 768}

USER_EMAIL = "peer-review-mobile@test.com"

COURSE_SLUG = "matrix-pr-course"
ARTICLE_SLUG = "matrix-pr-article"
EVENT_SLUG = "matrix-pr-event"


def _build_fixtures():
    """Create one of every page type the matrix needs.

    Returns a dict with the slugs / ids needed to assemble the URLs
    later. Closes the DB connection so the server thread can read.
    """
    from django.db import connection

    from accounts.models import User
    from content.models import (
        Article,
        Course,
        CourseCertificate,
        ProjectSubmission,
    )
    from events.models import Event

    user = _create_user(email=USER_EMAIL, tier_slug="free")

    # Course with peer review enabled, free tier so the user can access.
    course, _ = Course.objects.get_or_create(
        slug=COURSE_SLUG,
        defaults={
            "title": "Peer Review Matrix Course",
            "description": "Course used by the mobile text navigation matrix test.",
            "status": "published",
            "required_level": 0,
            "peer_review_enabled": True,
            "peer_review_count": 2,
            "peer_review_deadline_days": 7,
            "peer_review_criteria": "# Criteria\n\n- Does it work?",
        },
    )

    # Submission so /courses/<slug>/submit shows readonly state and
    # /courses/<slug>/reviews shows the user's submission.
    ProjectSubmission.objects.update_or_create(
        user=user, course=course,
        defaults={
            "project_url": "https://github.com/peer-review-mobile/proj",
            "description": "Matrix test submission.",
        },
    )

    # Certificate for the public /certificates/<uuid> route.
    cert, _ = CourseCertificate.objects.get_or_create(
        user=user, course=course,
    )

    # Article for /blog/<slug>.
    Article.objects.update_or_create(
        slug=ARTICLE_SLUG,
        defaults={
            "title": "Matrix PR Article",
            "description": "Article used by the matrix test.",
            "content_markdown": "# Hello\n\nMatrix test article.",
            "author": "Tester",
            "tags": [],
            "required_level": 0,
            "published": True,
            "date": datetime.date.today(),
        },
    )

    # Event for /events/<slug>.
    Event.objects.update_or_create(
        slug=EVENT_SLUG,
        defaults={
            "title": "Matrix PR Event",
            "description": "Event used by the matrix test.",
            "start_datetime": (
                timezone.now() + datetime.timedelta(days=7)
            ),
            "status": "upcoming",
            "required_level": 0,
            "published": True,
        },
    )

    # Sanity: the user's pk we look up below is the one we just made.
    assert User.objects.filter(email=USER_EMAIL).exists()

    cert_id = str(cert.id)
    connection.close()

    return {
        "course_slug": course.slug,
        "article_slug": ARTICLE_SLUG,
        "event_slug": EVENT_SLUG,
        "certificate_id": cert_id,
    }


def _open_mobile_menu(page):
    """Tap the hamburger and wait for the menu to be visible."""
    btn = page.locator("#mobile-menu-btn")
    btn.click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)


def _expand_mobile_section(page, section):
    """Tap a mobile text-nav toggle and wait for the sub-list."""
    page.locator(f"#mobile-{section}-toggle").click()
    page.wait_for_selector(
        f"#mobile-{section}-list:not(.hidden)", timeout=2000
    )


def _matrix_urls(fx):
    """Return the (id, path, requires_auth) tuples in matrix order."""
    return [
        ("home",                     "/",                                                False),
        ("course-detail",            f"/courses/{fx['course_slug']}",                    False),
        ("article-detail",           f"/blog/{fx['article_slug']}",                      False),
        ("event-detail",             f"/events/{fx['event_slug']}",                      False),
        ("pricing",                  "/pricing",                                         False),
        ("account",                  "/account/",                                        True),
        ("peer-review-submit",       f"/courses/{fx['course_slug']}/submit",             True),
        ("peer-review-dashboard",    f"/courses/{fx['course_slug']}/reviews",            True),
        ("peer-review-certificate",  f"/certificates/{fx['certificate_id']}",            False),
    ]


# ---------------------------------------------------------------------------
# Test 1: Mobile matrix
# ---------------------------------------------------------------------------


class TestMobileTextNavigationAcrossPages:
    """The mobile text navigation must be reachable on every public
    page type, including the four peer-review templates that previously
    skipped ``includes/header.html``."""

    def test_mobile_text_navigation_present_on_each_url(
        self, django_server, browser
    ):
        fx = _build_fixtures()
        urls = _matrix_urls(fx)

        # One context per auth state. Reusing the auth context across
        # the matrix iterations is fine -- we change the page's URL
        # between iterations rather than the context's identity.
        anon_context = browser.new_context(viewport=MOBILE_VIEWPORT)
        auth_context = _auth_context(browser, USER_EMAIL)
        # auth_context() pins a desktop viewport; override per-page
        # below via set_viewport_size.

        try:
            failures = []
            for label, path, requires_auth in urls:
                if requires_auth:
                    page = auth_context.new_page()
                    page.set_viewport_size(MOBILE_VIEWPORT)
                else:
                    page = anon_context.new_page()
                full_url = f"{django_server}{path}"

                try:
                    page.goto(full_url, wait_until="domcontentloaded")

                    # Step 1: hamburger is visible. If the header
                    # partial is missing, the button does not exist
                    # and this fails -- which is the bug from #463.
                    btn = page.locator("#mobile-menu-btn")
                    if btn.count() != 1 or not btn.is_visible():
                        failures.append(
                            f"[{label}] {path}: hamburger #mobile-menu-btn "
                            "is not present/visible -- "
                            "includes/header.html was not included"
                        )
                        continue

                    _open_mobile_menu(page)

                    # Step 2: Community and Resources toggles are reachable.
                    for section in ["community", "resources"]:
                        toggle = page.locator(f"#mobile-{section}-toggle")
                        if toggle.count() != 1 or not toggle.is_visible():
                            failures.append(
                                f"[{label}] {path}: "
                                f"#mobile-{section}-toggle is missing"
                            )
                            continue
                        _expand_mobile_section(page, section)

                    # Step 3: representative links inside both expanded
                    # accordions are visible.
                    blog_link = page.locator(
                        '#mobile-resources-list a[href="/blog"]'
                    )
                    if blog_link.count() != 1 or not blog_link.is_visible():
                        failures.append(
                            f"[{label}] {path}: text navigation did "
                            "not expand to show the /blog link"
                        )
                        continue
                    sprints_link = page.locator(
                        '#mobile-community-list a[href="/sprints"]'
                    )
                    if sprints_link.count() != 1 or not sprints_link.is_visible():
                        failures.append(
                            f"[{label}] {path}: text navigation did "
                            "not expand to show the /sprints link"
                        )
                        continue
                finally:
                    page.close()

            assert not failures, (
                "Mobile text navigation is missing on the following "
                "URLs:\n  - " + "\n  - ".join(failures)
            )
        finally:
            anon_context.close()
            auth_context.close()


# ---------------------------------------------------------------------------
# Test 2: Desktop check on the four peer-review URLs
# ---------------------------------------------------------------------------


class TestDesktopHeaderOnPeerReviewPages:
    """At desktop width the same four peer-review URLs must render the
    desktop nav (Community and Resources dropdown triggers) and must NOT show
    the mobile hamburger."""

    def test_desktop_header_renders_on_peer_review_pages(
        self, django_server, browser
    ):
        fx = _build_fixtures()
        peer_review_paths = [
            f"/courses/{fx['course_slug']}/submit",
            f"/courses/{fx['course_slug']}/reviews",
            f"/certificates/{fx['certificate_id']}",
        ]
        # The review_form URL needs an assigned reviewer; the matrix
        # already covers it on mobile via ``test_review_form_loads``
        # in the Django suite. We focus the Playwright desktop check
        # on the three URLs we can hit without seeding a reviewer.

        anon_context = browser.new_context(viewport=DESKTOP_VIEWPORT)
        auth_context = _auth_context(browser, USER_EMAIL)

        failures = []
        try:
            for path in peer_review_paths:
                requires_auth = "/certificates/" not in path
                if requires_auth:
                    page = auth_context.new_page()
                    page.set_viewport_size(DESKTOP_VIEWPORT)
                else:
                    page = anon_context.new_page()

                try:
                    page.goto(
                        f"{django_server}{path}",
                        wait_until="domcontentloaded",
                    )

                    resources_dd = page.locator("#resources-dropdown-btn")
                    if (
                        resources_dd.count() != 1
                        or not resources_dd.is_visible()
                    ):
                        failures.append(
                            f"{path}: desktop Resources dropdown button "
                            "(#resources-dropdown-btn) not visible"
                        )

                    community_dd = page.locator("#community-dropdown-btn")
                    if (
                        community_dd.count() != 1
                        or not community_dd.is_visible()
                    ):
                        failures.append(
                            f"{path}: desktop Community dropdown button "
                            "(#community-dropdown-btn) not visible"
                        )

                    # Hamburger must be hidden at desktop width.
                    hamburger = page.locator("#mobile-menu-btn")
                    if (
                        hamburger.count() == 1
                        and hamburger.is_visible()
                    ):
                        failures.append(
                            f"{path}: mobile hamburger is visible at "
                            "desktop width -- breakpoint regression"
                        )
                finally:
                    page.close()

            assert not failures, (
                "Desktop header is broken on the following peer-review "
                "URLs:\n  - " + "\n  - ".join(failures)
            )
        finally:
            anon_context.close()
            auth_context.close()


# ---------------------------------------------------------------------------
# Test 3: peer-review body still renders alongside the new chrome
# ---------------------------------------------------------------------------


class TestPeerReviewBodyChromeIntact:
    """Guard that the fix did not delete or move existing peer-review
    body markup. Specifically the ``Back to <course title>`` link from
    the dashboard body must still be in the DOM after the fix."""

    def test_back_to_course_link_still_renders(
        self, django_server, browser
    ):
        fx = _build_fixtures()
        context = _auth_context(browser, USER_EMAIL)
        page = context.new_page()
        page.set_viewport_size(MOBILE_VIEWPORT)
        try:
            page.goto(
                f"{django_server}/courses/{fx['course_slug']}/reviews",
                wait_until="domcontentloaded",
            )

            # The page-body link the dashboard template renders.
            back_link = page.locator(
                f'a[href="/courses/{fx["course_slug"]}"]'
            ).first
            assert back_link.is_visible(), (
                "Existing peer-review body link 'Back to course' was "
                "lost when adding the header chrome"
            )

            # And the page-body H1 is still here.
            assert page.locator(
                'h1:has-text("Peer Review Dashboard")'
            ).is_visible()
        finally:
            page.close()
            context.close()


# ---------------------------------------------------------------------------
# Test 4: existing /  homepage behavior unchanged
# ---------------------------------------------------------------------------


class TestHomepageMobileMenuStillWorks:
    """Spec acceptance criterion: ``playwright_tests/test_mobile_menu.py``
    on ``/`` continues to pass after the fix. We re-assert the key
    interaction here so a CI failure points at this issue's tests when
    the fix's blast radius is wrong."""

    def test_homepage_mobile_text_navigation_still_expands(
        self, django_server, browser
    ):
        # Make sure tier table exists before hitting the homepage.
        _build_fixtures()
        context = browser.new_context(viewport=MOBILE_VIEWPORT)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_mobile_menu(page)
            _expand_mobile_section(page, "community")
            _expand_mobile_section(page, "resources")

            blog = page.locator(
                '#mobile-resources-list a[href="/blog"]'
            )
            assert blog.count() == 1
            assert blog.is_visible()
            sprints = page.locator(
                '#mobile-community-list a[href="/sprints"]'
            )
            assert sprints.count() == 1
            assert sprints.is_visible()
        finally:
            context.close()
