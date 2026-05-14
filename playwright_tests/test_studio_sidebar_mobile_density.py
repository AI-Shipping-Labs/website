"""End-to-end coverage for the Studio sidebar drawer fixes on Pixel 7
(issue #624 — split out of #623).

The audit on #623 surfaced four must-fix problems for staff using Studio
on a 412 px-wide phone:

1. The drawer was 256 px on a 412 px viewport — read as a half-screen
   popover with the page bleeding through behind a backdrop.
2. The mobile sidebar toggle pill used non-token ``bg-gray-800``
   colors that floated as a dark island on the near-white page in light
   mode.
3. The seven section-header toggles (Events / Content / People / Planning /
   Communication / Tracking / Operations) used ``text-muted-foreground`` on the
   ``bg-card`` panel — read as disabled metadata in light mode.
4. The Users sub-row was the only split-button pattern in the sidebar
   — separate ``<a>`` and ``<button>`` halves with their own hover
   surfaces and a small ``px-2`` chevron tap target.

Tests below assert the post-fix behaviour at Pixel 7 (412x915) and
guard against the desktop layout regressing at 1280 px.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


PIXEL_7 = {"width": 412, "height": 915}
DESKTOP = {"width": 1280, "height": 900}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_drawer(page):
    """Open the mobile Studio drawer by clicking the toggle pill."""
    page.locator("#studio-sidebar-toggle").click()
    # Wait for the aside to no longer carry the bare ``hidden`` token.
    page.wait_for_function(
        "() => {"
        "  const aside = document.getElementById('studio-sidebar');"
        "  return aside && !aside.classList.contains('hidden');"
        "}"
    )


def _section_button(page, slug):
    return page.locator(
        f'#studio-sidebar-nav [aria-controls="studio-section-{slug}"]'
    )


def _section_list(page, slug):
    return page.locator(f'#studio-sidebar-nav #studio-section-{slug}')


def _has_class(class_str, name):
    """Whole-token class check (the aside class list contains substrings
    like ``overflow-hidden`` and ``md:hidden``; only the bare ``hidden``
    token controls drawer visibility)."""
    return name in (class_str or "").split()


def _set_theme_init(context, theme):
    """Pin the theme via an init script run before any page load.

    The theme toggle persists choice in ``localStorage['theme']`` and
    the global theme bootstrap reads that on first paint to add or
    remove the ``dark`` class on ``<html>``.
    """
    context.add_init_script(
        f"""
            localStorage.setItem('theme', '{theme}');
            document.documentElement.classList.toggle('dark', '{theme}' === 'dark');
        """
    )


# ---------------------------------------------------------------------------
# Scenario 1: drawer covers most of the screen on Pixel 7
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDrawerWidth:
    """The drawer width on a 412 px phone should be wide enough to read
    as a sheet (>=280 px) but narrow enough to leave a backdrop strip
    on the right (<=350 px)."""

    def test_drawer_width_at_pixel_7_is_between_280_and_350(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        _open_drawer(page)

        sidebar = page.locator("aside#studio-sidebar")
        box = sidebar.bounding_box()
        assert box is not None, "drawer should be visible after opening"
        # 18rem at the default 16px root font size = 288 px; the wider
        # ``min(85vw, 18rem)`` rule resolves to 288 px on Pixel 7
        # (350 px == 85% of 412 px > 288 px so the rem cap wins).
        assert box["width"] >= 280, (
            f"drawer width {box['width']} should be at least 280 px to "
            f"stop reading as a half-screen popover"
        )
        assert box["width"] <= 350, (
            f"drawer width {box['width']} should leave a visible "
            f"backdrop strip on the right at Pixel 7 width"
        )

        context.close()


# ---------------------------------------------------------------------------
# Scenario 2: toggle pill matches page chrome in light mode
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTogglePillLightMode:
    """The mobile toggle pill must not carry hard-coded dark gray
    classes — its background should resolve to the ``--card`` token so
    it blends with the page chrome in light mode."""

    def test_toggle_pill_uses_token_classes_not_gray_palette(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        _set_theme_init(context, "light")
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        toggle = page.locator("#studio-sidebar-toggle")
        cls = toggle.get_attribute("class") or ""

        for forbidden in ("bg-gray-800", "bg-gray-700", "text-gray-300"):
            assert forbidden not in cls.split(), (
                f"#studio-sidebar-toggle should not carry the legacy "
                f"non-token class {forbidden!r}; current classes: {cls!r}"
            )

        # Token classes are present.
        for required in ("bg-card", "text-foreground", "border-border"):
            assert required in cls.split(), (
                f"#studio-sidebar-toggle should carry token class "
                f"{required!r}; current classes: {cls!r}"
            )

        # The toggle's computed background-color matches the ``--card``
        # token (i.e. the same color the surrounding sidebar uses).
        toggle_bg = page.evaluate(
            "() => getComputedStyle(document.getElementById("
            "'studio-sidebar-toggle')).backgroundColor"
        )
        sidebar_bg = page.evaluate(
            "() => getComputedStyle(document.getElementById("
            "'studio-sidebar')).backgroundColor"
        )
        assert toggle_bg == sidebar_bg, (
            f"toggle background {toggle_bg!r} should match sidebar "
            f"background {sidebar_bg!r} (both are the ``--card`` token)"
        )

        context.close()


# ---------------------------------------------------------------------------
# Scenario 3: section-header toggles meet contrast + tap-target rules
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSectionHeaderContrastAndTapTarget:
    """All seven section toggle buttons should carry ``text-foreground/70``
    (lifted contrast vs. the previous ``text-muted-foreground``),
    ``font-semibold`` (lifted weight vs. ``font-medium``), and
    ``min-h-[44px]`` (44 px minimum tap target)."""

    SECTIONS = (
        "events", "content", "people", "planning",
        "communication", "tracking", "operations",
    )

    def test_section_toggles_use_lifted_contrast_and_meet_44px(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        _open_drawer(page)

        for slug in self.SECTIONS:
            button = _section_button(page, slug)
            assert button.count() == 1, f"missing section toggle: {slug!r}"

            cls = (button.get_attribute("class") or "").split()
            assert "text-foreground/70" in cls, (
                f"section {slug!r} should use ``text-foreground/70`` "
                f"(lifted contrast); current classes: {cls!r}"
            )
            assert "font-semibold" in cls, (
                f"section {slug!r} should use ``font-semibold`` "
                f"(lifted weight); current classes: {cls!r}"
            )
            assert "text-muted-foreground" not in cls, (
                f"section {slug!r} still carries the old "
                f"``text-muted-foreground`` class"
            )
            assert "font-medium" not in cls, (
                f"section {slug!r} still carries the old "
                f"``font-medium`` class"
            )
            assert "min-h-[44px]" in cls, (
                f"section {slug!r} should carry ``min-h-[44px]``; "
                f"current classes: {cls!r}"
            )

            box = button.bounding_box()
            assert box is not None
            assert box["height"] >= 44, (
                f"section {slug!r} computed height {box['height']} px "
                f"is below the 44 px tap-target minimum"
            )

            weight = page.evaluate(
                "(slug) => {"
                "  const sel = `#studio-sidebar-nav "
                "[aria-controls=\"studio-section-${slug}\"]`;"
                "  return parseInt(getComputedStyle("
                "document.querySelector(sel)).fontWeight, 10);"
                "}",
                slug,
            )
            assert weight >= 600, (
                f"section {slug!r} computed font-weight {weight} should "
                f"be at least 600 (font-semibold)"
            )

        context.close()


# ---------------------------------------------------------------------------
# Scenario 4: Users row is a single tap target, not a split button
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUsersRowSingleTapTarget:
    """The Users row should be exactly one ``<a>`` pointing at
    ``/studio/users/`` — no nested ``<button data-studio-users-toggle>``,
    no second hover surface, no chevron sub-toggle."""

    def test_users_row_is_one_link_one_hover_surface(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        _open_drawer(page)

        # Expand People to surface the Users row.
        _section_button(page, "people").click()

        # Exactly one Users anchor.
        users_anchors = page.locator(
            '#studio-section-people > li > a[href="/studio/users/"]'
        )
        assert users_anchors.count() == 1, (
            f"expected exactly one Users <a>, got {users_anchors.count()}"
        )

        # Zero chevron sub-toggle <button>s anywhere in the sidebar.
        chevron_buttons = page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        )
        assert chevron_buttons.count() == 0, (
            "the Users sub-toggle <button> should be removed entirely"
        )

        # The anchor itself carries no inline child <button> sibling
        # — the row is a single tap target.
        users_li = page.locator(
            '#studio-section-people > li:has(> a[href="/studio/users/"])'
        )
        button_children = users_li.locator("> button")
        assert button_children.count() == 0, (
            "Users row should have no sibling <button>; the chevron "
            "sub-toggle pattern was removed in #624"
        )

        # Tapping the row navigates to /studio/users/.
        users_anchors.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/users/" in page.url

        context.close()


# ---------------------------------------------------------------------------
# Scenario 5: desktop layout does not regress
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDesktopUnchanged:
    """At 1280 px the sticky sidebar is still 256 px wide and the
    mobile toggle pill is hidden."""

    def test_desktop_sidebar_is_256px_and_toggle_is_hidden(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size(DESKTOP)
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        sidebar = page.locator("aside#studio-sidebar")
        assert sidebar.is_visible()
        box = sidebar.bounding_box()
        assert box is not None
        # ``md:w-64`` resolves to 16 rem == 256 px (allow a 2 px tolerance
        # for sub-pixel rounding by the layout engine).
        assert 254 <= box["width"] <= 258, (
            f"desktop sidebar width {box['width']} should be ~256 px; "
            f"the ``w-[min(85vw,18rem)] md:w-64`` rule must keep the "
            f"desktop column unchanged"
        )

        # The mobile toggle pill is hidden at md+ (``md:hidden`` class).
        toggle = page.locator("#studio-sidebar-toggle")
        assert not toggle.is_visible(), (
            "mobile toggle pill should be hidden at >=768 px viewports"
        )

        context.close()


# ---------------------------------------------------------------------------
# Scenario 6: drawer closes on backdrop tap and on link navigation
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDrawerCloseBehaviour:
    """The drawer should close when the user taps the backdrop and
    after navigating via a sidebar link."""

    def test_backdrop_tap_closes_and_link_tap_navigates_then_closes(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        _open_drawer(page)
        sidebar = page.locator("aside#studio-sidebar")
        assert not _has_class(sidebar.get_attribute("class"), "hidden")

        # Backdrop tap closes the drawer.
        # Click outside the 288px-wide drawer to avoid hitting the drawer
        # itself (the backdrop covers the full viewport).
        page.locator("#studio-backdrop").click(position={"x": 380, "y": 400})
        page.wait_for_function(
            "() => document.getElementById('studio-sidebar')"
            ".classList.contains('hidden')"
        )
        assert _has_class(sidebar.get_attribute("class"), "hidden")

        # Re-open and navigate via a Content > Articles link.
        _open_drawer(page)
        _section_button(page, "content").click()
        page.locator(
            '#studio-section-content a[href="/studio/articles/"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/articles/" in page.url

        # After navigation, the drawer is hidden again on the new page.
        sidebar_after = page.locator("aside#studio-sidebar")
        assert _has_class(sidebar_after.get_attribute("class"), "hidden")

        context.close()
