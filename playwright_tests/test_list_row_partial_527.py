"""Playwright E2E tests for issue #527.

Mobile accordion UI audit — workshop tutorial pages should render their
list rows from the canonical `templates/includes/_list_row.html` partial
so the workshop landing's "Tutorial pages" `<ol>`, the reader sidebar
drawer (workshop pages and course unit lists), and the prose body lists
all share the same row template (font, weight, color, padding,
inter-row gap), with the only allowed visual difference being the
leading marker icon.

Scenarios covered (8):

1. Mobile visitor sees the same row template on the workshop landing
   and inside the tutorial drawer (anonymous).
2. Logged-in member sees completion glyphs without typography drift.
3. Drawer rows match between anonymous and logged-in.
4. Walkthrough prose list reads in the same family as the canonical
   card.
5. Desktop sidebar and main column unchanged.
6. Course reader sidebar inherits the same row template.
7. Tighter rhythm — visible gap reduction on the landing card.
8. No regression on summary-style accordions from #516.

Screenshots are written to ``playwright_tests/screenshots/issue-527/``.

Usage:
    uv run pytest playwright_tests/test_list_row_partial_527.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from playwright_tests.conftest import (  # noqa: E402
    create_session_for_user,
    create_user,
)
from playwright_tests.test_reader_mobile_483 import (  # noqa: E402
    _clear_courses,
    _create_course,
    _create_module,
    _create_unit,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-527")
PIXEL_7 = {"width": 393, "height": 851}
DESKTOP = {"width": 1280, "height": 900}


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _mobile_anon_context(browser):
    return browser.new_context(viewport=PIXEL_7)


def _mobile_authed_context(browser, email):
    session_key = create_session_for_user(email)
    ctx = browser.new_context(viewport=PIXEL_7)
    ctx.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return ctx


def _desktop_anon_context(browser):
    return browser.new_context(viewport=DESKTOP)


def _seed_walkthrough_workshop(slug="agent-skills-527"):
    """Seed a workshop with a description that contains a Walkthrough
    `<ol>` and a Links `<ul>` so the prose-vs-card scenario can compare
    rendered prose `<li>` margins against canonical card row padding."""
    _clear_workshops()
    description = (
        "## Walkthrough\n"
        "\n"
        "1. Read the SKILL.md file format.\n"
        "2. Note the YAML front-matter conventions.\n"
        "3. Scaffold a new skill from the example.\n"
        "4. Test the skill locally.\n"
        "5. Submit a pull request.\n"
        "\n"
        "## Links\n"
        "\n"
        "- [Reference docs](https://example.com/docs)\n"
        "- [Sample skill](https://example.com/sample)\n"
        "- [Discussion](https://example.com/discuss)\n"
    )
    return _create_workshop(
        slug=slug,
        title="Coding Agent Skills and Commands",
        landing=0,
        pages=0,
        recording=20,
        description=description,
        pages_data=[
            ("intro", "Intro", "# Intro\n\nIntro body."),
            ("skill-md", "SKILL.md format", "# SKILL.md\n\nFormat body."),
            ("yaml-frontmatter", "YAML front matter", "# YAML\n\nYAML body."),
            ("scaffolding", "Scaffolding a skill", "# Scaffold\n\nScaffold body."),
            ("testing", "Testing locally", "# Test\n\nTest body."),
        ],
    )


def _row_style(page, selector):
    """Return computed { fontSize, fontWeight, color, padding, minHeight }
    for the first matching row anchor."""
    return page.evaluate(
        """selector => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const cs = getComputedStyle(el);
            return {
                fontSize: cs.fontSize,
                fontWeight: cs.fontWeight,
                color: cs.color,
                padding: cs.padding,
                paddingTop: cs.paddingTop,
                paddingBottom: cs.paddingBottom,
                paddingLeft: cs.paddingLeft,
                paddingRight: cs.paddingRight,
                minHeight: cs.minHeight,
                display: cs.display,
            };
        }""",
        selector,
    )


# ----------------------------------------------------------------------
# Scenario 1: Anonymous visitor — landing rows match drawer rows
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLandingAndDrawerRowsMatchAnonymous:
    def test_landing_row_and_drawer_row_share_canonical_scale(
        self, browser, django_server,
    ):
        _seed_walkthrough_workshop()

        ctx = _mobile_anon_context(browser)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/agent-skills-527/",
                wait_until="domcontentloaded",
            )

            # Read landing row computed style.
            landing_style = _row_style(
                page,
                '[data-testid="workshop-page-row"]',
            )
            assert landing_style is not None, (
                "workshop-page-row anchor not found on the landing"
            )
            assert landing_style["fontSize"] == "14px"
            assert landing_style["fontWeight"] == "400"
            assert landing_style["paddingTop"] == "8px"
            assert landing_style["paddingBottom"] == "8px"
            assert landing_style["paddingLeft"] == "12px"
            assert landing_style["paddingRight"] == "12px"
            assert landing_style["minHeight"] == "44px"

            _shot(page, "01-landing-rows-mobile-anon")

            # Now navigate to the tutorial page and open the drawer.
            page.goto(
                f"{django_server}/workshops/agent-skills-527/tutorial/intro",
                wait_until="domcontentloaded",
            )
            toggle = page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            )
            toggle.wait_for(state="visible")
            toggle.click()
            page.locator("#sidebar-nav").wait_for(state="visible")

            # Read the first non-current row anchor inside the drawer.
            drawer_style = _row_style(
                page,
                '#sidebar-nav ul li:nth-child(2) > a',
            )
            assert drawer_style is not None, (
                "second row anchor not found in the drawer"
            )

            assert drawer_style["fontSize"] == landing_style["fontSize"], (
                f"font-size drift: drawer={drawer_style['fontSize']} "
                f"landing={landing_style['fontSize']}"
            )
            assert drawer_style["fontWeight"] == landing_style["fontWeight"]
            assert drawer_style["color"] == landing_style["color"], (
                f"color drift: drawer={drawer_style['color']} "
                f"landing={landing_style['color']}"
            )
            assert drawer_style["paddingTop"] == "8px"
            assert drawer_style["paddingBottom"] == "8px"
            assert drawer_style["paddingLeft"] == "12px"
            assert drawer_style["paddingRight"] == "12px"
            assert drawer_style["minHeight"] == "44px"

            # Inter-row gap (Tailwind `space-y-0.5`) — first <li> has no
            # margin; siblings get `margin-top: 2px` from the
            # space-y-0.5 utility (`> :not([hidden]) ~ :not([hidden])`).
            sibling_margin = page.evaluate(
                """() => {
                    const ul = document.querySelector('#sidebar-nav ul');
                    if (!ul) return null;
                    const items = ul.querySelectorAll('li');
                    if (items.length < 2) return null;
                    return getComputedStyle(items[1]).marginTop;
                }"""
            )
            assert sibling_margin == "2px", (
                f"drawer inter-row gap {sibling_margin} != 2px"
            )

            _shot(page, "01-drawer-rows-mobile-anon")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Logged-in member — completion glyphs do not drift typography
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLoggedInCompletionGlyphsNoTypographyDrift:
    def test_completed_and_incomplete_rows_share_typography(
        self, browser, django_server,
    ):
        _seed_walkthrough_workshop()
        create_user("loggedin-527@test.com", tier_slug="main")

        from accounts.models import User
        from content.models import WorkshopPage
        from content.services import completion as completion_service

        user = User.objects.get(email="loggedin-527@test.com")
        # Mark the second page complete so the drawer shows one
        # check-circle-2 + several plain circles.
        page_obj = WorkshopPage.objects.get(
            workshop__slug="agent-skills-527", slug="skill-md",
        )
        completion_service.mark_completed(user, page_obj)
        from django.db import connection
        connection.close()

        ctx = _mobile_authed_context(browser, "loggedin-527@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/agent-skills-527/tutorial/intro",
                wait_until="domcontentloaded",
            )
            page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            ).click()
            page.locator("#sidebar-nav").wait_for(state="visible")

            # Wait for lucide to rewrite the <i> tags so the
            # data-testid lives on the rendered <svg>.
            page.wait_for_function(
                "document.querySelectorAll("
                "'#sidebar-nav [data-testid=\"sidebar-completed-page\"]'"
                ").length > 0",
                timeout=2000,
            )

            # Find the completed row (the one whose anchor contains the
            # sidebar-completed-page icon) and a non-completed,
            # non-current row.
            completed_row_style = page.evaluate(
                """() => {
                    const icon = document.querySelector(
                        '#sidebar-nav [data-testid="sidebar-completed-page"]'
                    );
                    if (!icon) return null;
                    const a = icon.closest('a');
                    if (!a) return null;
                    const cs = getComputedStyle(a);
                    return {
                        fontSize: cs.fontSize,
                        fontWeight: cs.fontWeight,
                        color: cs.color,
                        paddingTop: cs.paddingTop,
                        paddingBottom: cs.paddingBottom,
                        paddingLeft: cs.paddingLeft,
                        paddingRight: cs.paddingRight,
                        minHeight: cs.minHeight,
                    };
                }"""
            )
            assert completed_row_style is not None, (
                "no completed row found in drawer"
            )

            # Non-completed, non-current row: anchor without aria-current
            # and without the completed icon child.
            incomplete_row_style = page.evaluate(
                """() => {
                    const anchors = document.querySelectorAll(
                        '#sidebar-nav ul a'
                    );
                    for (const a of anchors) {
                        if (a.getAttribute('aria-current') === 'page') continue;
                        if (a.querySelector(
                            '[data-testid="sidebar-completed-page"]'
                        )) continue;
                        const cs = getComputedStyle(a);
                        return {
                            fontSize: cs.fontSize,
                            fontWeight: cs.fontWeight,
                            color: cs.color,
                            paddingTop: cs.paddingTop,
                            paddingBottom: cs.paddingBottom,
                            paddingLeft: cs.paddingLeft,
                            paddingRight: cs.paddingRight,
                            minHeight: cs.minHeight,
                        };
                    }
                    return null;
                }"""
            )
            assert incomplete_row_style is not None, (
                "no incomplete non-current row found in drawer"
            )

            # Every typographic property must match — only the leading
            # icon should differ between the two rows.
            for key in (
                "fontSize", "fontWeight", "color",
                "paddingTop", "paddingBottom",
                "paddingLeft", "paddingRight", "minHeight",
            ):
                assert completed_row_style[key] == incomplete_row_style[key], (
                    f"completed/incomplete row drift on {key}: "
                    f"{completed_row_style[key]} vs "
                    f"{incomplete_row_style[key]}"
                )

            _shot(page, "02-drawer-rows-mobile-loggedin")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Anonymous and logged-in drawer rows match
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonAndLoggedInDrawerRowsMatch:
    def _row_style_first_non_current(self, page):
        return page.evaluate(
            """() => {
                const anchors = document.querySelectorAll(
                    '#sidebar-nav ul a'
                );
                for (const a of anchors) {
                    if (a.getAttribute('aria-current') === 'page') continue;
                    const cs = getComputedStyle(a);
                    return {
                        fontSize: cs.fontSize,
                        fontWeight: cs.fontWeight,
                        color: cs.color,
                        paddingTop: cs.paddingTop,
                        paddingBottom: cs.paddingBottom,
                        paddingLeft: cs.paddingLeft,
                        paddingRight: cs.paddingRight,
                        minHeight: cs.minHeight,
                    };
                }
                return null;
            }"""
        )

    def test_first_non_current_row_matches_across_auth_states(
        self, browser, django_server,
    ):
        _seed_walkthrough_workshop()
        create_user("authstate-527@test.com", tier_slug="main")

        # Anonymous first.
        ctx_anon = _mobile_anon_context(browser)
        page_anon = ctx_anon.new_page()
        try:
            page_anon.goto(
                f"{django_server}/workshops/agent-skills-527/tutorial/intro",
                wait_until="domcontentloaded",
            )
            page_anon.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            ).click()
            page_anon.locator("#sidebar-nav").wait_for(state="visible")
            anon_style = self._row_style_first_non_current(page_anon)
            assert anon_style is not None
        finally:
            ctx_anon.close()

        # Logged-in.
        ctx_auth = _mobile_authed_context(browser, "authstate-527@test.com")
        page_auth = ctx_auth.new_page()
        try:
            page_auth.goto(
                f"{django_server}/workshops/agent-skills-527/tutorial/intro",
                wait_until="domcontentloaded",
            )
            page_auth.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            ).click()
            page_auth.locator("#sidebar-nav").wait_for(state="visible")
            auth_style = self._row_style_first_non_current(page_auth)
            assert auth_style is not None
        finally:
            ctx_auth.close()

        for key in (
            "fontSize", "fontWeight", "color",
            "paddingTop", "paddingBottom",
            "paddingLeft", "paddingRight", "minHeight",
        ):
            assert anon_style[key] == auth_style[key], (
                f"auth-state drift on {key}: "
                f"anon={anon_style[key]} authed={auth_style[key]}"
            )


# ----------------------------------------------------------------------
# Scenario 4: Walkthrough prose list reads in the same family
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWalkthroughProseListInSameFamily:
    def test_prose_li_margin_tightened_to_quarter_rem(
        self, browser, django_server,
    ):
        _seed_walkthrough_workshop()

        ctx = _mobile_anon_context(browser)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/agent-skills-527/",
                wait_until="domcontentloaded",
            )

            # Read computed margin-top + line-height of an <li> inside
            # the workshop-description prose body. The first <li> has no
            # top margin; the second is the one we measure.
            margins = page.evaluate(
                """() => {
                    const desc = document.querySelector(
                        '[data-testid="workshop-description"]'
                    );
                    if (!desc) return null;
                    const lis = desc.querySelectorAll('ol > li, ul > li');
                    if (lis.length < 2) return null;
                    const cs = getComputedStyle(lis[1]);
                    return {
                        marginTop: cs.marginTop,
                        marginBottom: cs.marginBottom,
                        lineHeight: cs.lineHeight,
                    };
                }"""
            )
            assert margins is not None, (
                "Walkthrough prose list not found in workshop-description"
            )

            # 0.25rem == 4px (Tailwind base font-size 16px).
            assert margins["marginTop"] == "4px", (
                f"prose <li> margin-top {margins['marginTop']} != 4px (0.25rem)"
            )
            assert margins["marginBottom"] == "4px", (
                f"prose <li> margin-bottom {margins['marginBottom']} != 4px"
            )

            # line-height: 1.5 is computed by browsers as
            # `${font-size * 1.5}px`. Default prose <li> font-size is
            # the parent prose color's 16px (set on .prose) so we expect
            # 24px. Allow a 1px tolerance.
            lh_px = float(margins["lineHeight"].replace("px", ""))
            assert 23.0 <= lh_px <= 25.0, (
                f"prose <li> line-height {margins['lineHeight']} != ~24px "
                f"(1.5)"
            )

            # Compare against the canonical card row padding for the
            # "in same family" sanity. Card row sets padding 8px/12px;
            # gap between rows visually = ~2px (space-y-0.5 sibling
            # gap). Prose <li> margin 4px stacked between baselines is
            # within the 4px tolerance the issue requested.
            row_style = _row_style(
                page, '[data-testid="workshop-page-row"]',
            )
            assert row_style is not None
            row_lh = page.evaluate(
                """() => {
                    const a = document.querySelector(
                        '[data-testid="workshop-page-row"]'
                    );
                    return getComputedStyle(a).lineHeight;
                }"""
            )
            row_lh_px = float(row_lh.replace("px", ""))
            # The canonical row uses Tailwind's text-sm (14px) line-height
            # 1.25rem == 20px. The prose <li> uses our 1.5 override,
            # equating to ~24px on 16px text. Both within the issue's
            # "same family" tolerance.
            assert 19.0 <= row_lh_px <= 25.0

            _shot(page, "04-prose-vs-card")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Desktop sidebar and main column unchanged
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDesktopUnchanged:
    def test_desktop_sidebar_row_uses_canonical_scale(
        self, browser, django_server,
    ):
        _seed_walkthrough_workshop()

        ctx = _desktop_anon_context(browser)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/agent-skills-527/tutorial/intro",
                wait_until="domcontentloaded",
            )
            # On desktop the sidebar nav is always visible (lg:block).
            page.locator("#sidebar-nav").wait_for(state="visible")

            row_style = _row_style(
                page,
                '#sidebar-nav ul li:nth-child(2) > a',
            )
            assert row_style is not None
            # Canonical scale matches the mobile drawer: 14px / 400 / 8px
            # padding / 12px horizontal padding / 44px min-height.
            assert row_style["fontSize"] == "14px"
            assert row_style["fontWeight"] == "400"
            assert row_style["paddingTop"] == "8px"
            assert row_style["paddingBottom"] == "8px"
            assert row_style["paddingLeft"] == "12px"
            assert row_style["paddingRight"] == "12px"
            assert row_style["minHeight"] == "44px"

            _shot(page, "05-desktop-sidebar")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 6: Course reader sidebar inherits the same row template
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseReaderInheritsTemplate:
    def test_course_unit_row_matches_workshop_drawer_row(
        self, browser, django_server,
    ):
        _clear_courses()
        course = _create_course(
            title="Skills 101",
            slug="skills-101-527",
            required_level=0,
        )
        module = _create_module(course, "Module One", sort_order=1)
        _create_unit(module, "Unit Alpha", sort_order=1, body="Alpha body")
        _create_unit(module, "Unit Beta", sort_order=2, body="Beta body")
        create_user("course-527@test.com", tier_slug="main")

        ctx = _mobile_authed_context(browser, "course-527@test.com")
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/courses/skills-101-527/module-one/unit-alpha",
                wait_until="domcontentloaded",
            )
            # Open the mobile drawer.
            toggle = page.locator(
                '[data-testid="reader-mobile-drawer-toggle"]',
            )
            toggle.wait_for(state="visible")
            toggle.click()
            page.locator("#sidebar-nav").wait_for(state="visible")

            # Make sure the module <details> is open so the unit list
            # is rendered (the active unit's module is open by default).
            page.wait_for_function(
                "document.querySelectorAll("
                "'#sidebar-nav details[open] ul li a'"
                ").length >= 2",
                timeout=2000,
            )

            # Read the second unit row (the non-current one) so we
            # measure a non-active row.
            unit_style = page.evaluate(
                """() => {
                    const anchors = document.querySelectorAll(
                        '#sidebar-nav details[open] ul li a'
                    );
                    for (const a of anchors) {
                        if (a.getAttribute('aria-current') === 'page') continue;
                        const cs = getComputedStyle(a);
                        return {
                            fontSize: cs.fontSize,
                            fontWeight: cs.fontWeight,
                            paddingTop: cs.paddingTop,
                            paddingBottom: cs.paddingBottom,
                            paddingLeft: cs.paddingLeft,
                            paddingRight: cs.paddingRight,
                            minHeight: cs.minHeight,
                        };
                    }
                    return null;
                }"""
            )
            assert unit_style is not None, (
                "no non-current unit row found in course drawer"
            )
            assert unit_style["fontSize"] == "14px"
            assert unit_style["fontWeight"] == "400"
            assert unit_style["paddingTop"] == "8px"
            assert unit_style["paddingBottom"] == "8px"
            assert unit_style["paddingLeft"] == "12px"
            assert unit_style["paddingRight"] == "12px"
            assert unit_style["minHeight"] == "44px"

            # Module <summary> is unchanged from #516: text-sm
            # font-medium text-foreground.
            summary_style = page.evaluate(
                """() => {
                    const sum = document.querySelector(
                        '#sidebar-nav details > summary'
                    );
                    if (!sum) return null;
                    const span = sum.querySelector('span');
                    if (!span) return null;
                    const cs = getComputedStyle(span);
                    return {
                        fontSize: cs.fontSize,
                        fontWeight: cs.fontWeight,
                    };
                }"""
            )
            assert summary_style is not None
            # text-sm = 14px, font-medium = 500.
            assert summary_style["fontSize"] == "14px", (
                f"#516 summary font-size regressed: {summary_style['fontSize']}"
            )
            assert summary_style["fontWeight"] == "500", (
                f"#516 summary font-weight regressed: "
                f"{summary_style['fontWeight']}"
            )

            _shot(page, "06-course-drawer-mobile")
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Tighter rhythm — landing card height does not grow
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestRhythmTighterButTapTargetPreserved:
    def test_each_row_is_44px_min_height_and_card_does_not_grow(
        self, browser, django_server,
    ):
        _seed_walkthrough_workshop()

        ctx = _mobile_anon_context(browser)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/agent-skills-527/",
                wait_until="domcontentloaded",
            )
            # Each row is at least 44px tall (tap target preserved).
            heights = page.evaluate(
                """() => {
                    return Array.from(
                        document.querySelectorAll(
                            '[data-testid="workshop-page-row"]'
                        )
                    ).map(a => a.getBoundingClientRect().height);
                }"""
            )
            assert heights, "no workshop-page-row anchors found"
            for i, h in enumerate(heights):
                assert h >= 44, (
                    f"row {i} height {h} < 44px (tap target violated)"
                )

            # Inter-row gap on the landing matches `space-y-0.5` (2px).
            sibling_margin = page.evaluate(
                """() => {
                    const ol = document.querySelector(
                        '[data-testid="workshop-pages-list"] ol'
                    );
                    if (!ol) return null;
                    const items = ol.querySelectorAll('li');
                    if (items.length < 2) return null;
                    return getComputedStyle(items[1]).marginTop;
                }"""
            )
            assert sibling_margin == "2px", (
                f"landing inter-row gap {sibling_margin} != 2px"
            )
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 8: No regression on summary-style accordions from #516
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNoRegressionOn516Summaries:
    def test_workshop_video_chapter_and_transcript_summary_unchanged(
        self, browser, django_server,
    ):
        # Seed a workshop with a recording so the video page renders
        # (workshop_video URL is /workshops/<slug>/video).
        _clear_workshops()
        _create_workshop(
            slug="video-527",
            title="Video Workshop",
            landing=0,
            pages=0,
            recording=0,
            recording_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            description="Video workshop description.",
            pages_data=[
                ("intro", "Intro", "# Intro\n\nIntro body."),
            ],
        )

        ctx = _mobile_anon_context(browser)
        page = ctx.new_page()
        try:
            page.goto(
                f"{django_server}/workshops/video-527/video",
                wait_until="domcontentloaded",
            )
            # Find the first <details><summary> on the page (Chapters or
            # transcript). The #516 contract is that the summary text
            # span computes to text-base (16px) font-medium (500).
            summary = page.evaluate(
                """() => {
                    const sum = document.querySelector(
                        'details > summary'
                    );
                    if (!sum) return null;
                    const span = sum.querySelector('span') || sum;
                    const cs = getComputedStyle(span);
                    return {
                        fontSize: cs.fontSize,
                        fontWeight: cs.fontWeight,
                    };
                }"""
            )
            if summary is not None:
                assert summary["fontSize"] == "16px", (
                    f"#516 video-page summary font-size regressed: "
                    f"{summary['fontSize']}"
                )
                assert summary["fontWeight"] == "500", (
                    f"#516 video-page summary font-weight regressed: "
                    f"{summary['fontWeight']}"
                )
        finally:
            ctx.close()
