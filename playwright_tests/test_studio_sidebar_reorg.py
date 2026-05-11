"""End-to-end tests for the reorganised Studio sidebar (issue #570).

The Studio sidebar is split into a small top utility row
(``Back to website`` + theme toggle), a Dashboard link, and five
collapsible sections — Content, People, Events, Marketing, Operations.
The section that contains the current page auto-expands server-side; on
the dashboard only Content is open.

Coverage mirrors the 11 Playwright scenarios in the issue body. Per the
project testing guidelines we assert on specific elements (visible
buttons, links, aria attributes) rather than full HTML substring
matches.
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
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section_button(page, slug):
    """Return the section-toggle <button> for the given slug."""
    return page.locator(
        f'#studio-sidebar-nav [aria-controls="studio-section-{slug}"]'
    )


def _section_list(page, slug):
    """Return the <ul> body for the given section slug."""
    return page.locator(f'#studio-sidebar-nav #studio-section-{slug}')


def _has_class(class_str, name):
    """Return True if ``name`` is a whole class token in ``class_str``.

    The sidebar's class list contains substrings like ``overflow-hidden``
    and ``md:hidden`` — a naïve ``"hidden" in class_str`` substring check
    would match those even when the bare ``hidden`` class is absent. We
    split on whitespace and look for an exact token match.
    """
    return name in (class_str or "").split()


def _create_non_superuser_staff(email):
    """Create a staff user that is NOT a superuser.

    The shared ``create_staff_user`` helper sets ``is_superuser=True``.
    For the access-control test we need a plain staff user — we use
    ``create_user(is_staff=True)`` instead.
    """
    return _create_user(email, is_staff=True)


# ---------------------------------------------------------------------------
# Scenario 1: focused nav on first load
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffLandsInStudio:
    """Staff member lands in Studio and sees a focused nav."""

    def test_dashboard_renders_focused_sidebar(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Top utility row precedes the section groups.
        back = page.locator(
            'aside#studio-sidebar a[href="/"]:has(span:text-is("Back to website"))'
        )
        theme = page.locator('aside#studio-sidebar [data-testid="theme-toggle"]')
        first_section = _section_button(page, "content")

        assert back.count() == 1
        assert theme.count() == 1
        assert first_section.count() == 1

        back_y = back.bounding_box()["y"]
        theme_y = theme.bounding_box()["y"]
        section_y = first_section.bounding_box()["y"]
        assert back_y < theme_y < section_y, (
            f"Expected order Back -> Theme -> Section, got "
            f"y={back_y}, {theme_y}, {section_y}"
        )

        # Content is expanded — its six child links are visible.
        for label in [
            "Articles",
            "Courses",
            "Projects",
            "Workshops",
            "Recordings",
            "Downloads",
        ]:
            link = page.locator(
                f'#studio-section-content a:has(span:text-is("{label}"))'
            )
            assert link.count() == 1
            assert link.is_visible(), f"{label!r} should be visible in expanded Content"

        # The other four sections are collapsed — their <ul> bodies are not visible.
        for slug in ("people", "events", "marketing", "operations"):
            ul = _section_list(page, slug)
            assert ul.count() == 1
            assert not ul.is_visible(), (
                f"Section {slug!r} should be collapsed on dashboard"
            )

        # v{VERSION} appears once at the bottom of the sidebar.
        version_line = page.locator(
            'aside#studio-sidebar p:has-text("v")'
        ).filter(has_text="v")
        assert version_line.count() >= 1


# ---------------------------------------------------------------------------
# Scenario 2: opening a collapsed section to find a tool
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSectionToggleAndNavigate:
    """Clicking a collapsed section header reveals its children and
    toggles ``aria-expanded``; the section also auto-expands on the
    target page after navigation."""

    def test_open_close_open_then_navigate(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        button = _section_button(page, "operations")
        ul = _section_list(page, "operations")

        # Initially collapsed.
        assert button.get_attribute("aria-expanded") == "false"
        assert not ul.is_visible()

        # First click expands.
        button.click()
        assert button.get_attribute("aria-expanded") == "true"
        for label in ["Content sync", "Worker", "Redirects", "Settings"]:
            assert page.locator(
                f'#studio-section-operations a:has(span:text-is("{label}"))'
            ).is_visible()

        # Second click collapses.
        button.click()
        assert button.get_attribute("aria-expanded") == "false"
        assert not ul.is_visible()

        # Re-open, then click Content sync.
        button.click()
        page.locator(
            '#studio-section-operations a:has(span:text-is("Content sync"))'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/sync/" in page.url

        # Operations is expanded on the new page (no clicks needed).
        button_after = _section_button(page, "operations")
        assert button_after.get_attribute("aria-expanded") == "true"
        assert _section_list(page, "operations").is_visible()


# ---------------------------------------------------------------------------
# Scenario 3: navigating to a People page auto-expands it
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPeopleAutoExpands:
    """Deep-linking to a People page auto-expands the section and
    applies the active-link highlight to the right item."""

    def test_crm_page_auto_expands_people_section(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/crm/", wait_until="domcontentloaded")

        # People <ul> is visible without any clicks.
        people_button = _section_button(page, "people")
        people_ul = _section_list(page, "people")
        assert people_button.get_attribute("aria-expanded") == "true"
        assert people_ul.is_visible()

        for label in ["Users", "CRM", "Sprints", "Plans"]:
            assert page.locator(
                f'#studio-section-people a:has(span:text-is("{label}"))'
            ).count() >= 1

        # Active highlight on the CRM link.
        crm_link = page.locator(
            '#studio-section-people a[href="/studio/crm/"]'
        )
        assert "bg-secondary" in (crm_link.get_attribute("class") or "")

        # Content is also expanded (default); other sections collapsed.
        assert _section_list(page, "content").is_visible()
        for slug in ("events", "marketing", "operations"):
            assert not _section_list(page, slug).is_visible()


# ---------------------------------------------------------------------------
# Scenario 4: deep-link into Imports — both wrappers auto-expand
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestImportsAutoExpandsBothWrappers:
    """Navigating to /studio/imports/ expands the People section AND
    the Users sub-group, and shows superuser-only ``New user``."""

    def test_imports_deep_link_expands_people_and_users(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")  # superuser

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/imports/", wait_until="domcontentloaded"
        )

        assert _section_list(page, "people").is_visible()
        users_children = page.locator("#studio-users-children")
        assert users_children.is_visible()
        users_chevron = page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        )
        assert users_chevron.get_attribute("aria-expanded") == "true"

        imports_link = page.locator(
            '#studio-users-children a[href="/studio/imports/"]'
        )
        assert "bg-secondary" in (imports_link.get_attribute("class") or "")

        # New user link is rendered (superuser only).
        new_user = page.locator(
            '#studio-users-children a[href="/studio/users/new/"]'
        )
        assert new_user.count() == 1
        assert new_user.is_visible()


# ---------------------------------------------------------------------------
# Scenario 5: non-superuser cannot see superuser-only items
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNonSuperuserGating:
    """Non-superuser staff do not see ``New user`` or ``API tokens``."""

    def test_users_subgroup_omits_new_user_for_non_superuser(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_non_superuser_staff("plainstaff@test.com")

        context = _auth_context(browser, "plainstaff@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/users/", wait_until="domcontentloaded"
        )

        # Users sub-group present.
        users_children = page.locator("#studio-users-children")
        assert users_children.count() == 1

        # Imports + Tier overrides visible; New user not rendered at all.
        assert page.locator(
            '#studio-users-children a[href="/studio/imports/"]'
        ).count() == 1
        assert page.locator(
            '#studio-users-children a[href="/studio/users/tier-override/"]'
        ).count() == 1
        assert page.locator(
            '#studio-users-children a[href="/studio/users/new/"]'
        ).count() == 0

    def test_operations_omits_api_tokens_for_non_superuser(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_non_superuser_staff("plainstaff@test.com")

        context = _auth_context(browser, "plainstaff@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Expand Operations so its children are flowed into the DOM.
        _section_button(page, "operations").click()

        for label in ["Content sync", "Worker", "Redirects", "Settings"]:
            assert page.locator(
                f'#studio-section-operations a:has(span:text-is("{label}"))'
            ).count() == 1

        # API tokens link is not present.
        assert page.locator(
            '#studio-section-operations [data-testid="api-tokens-nav-link"]'
        ).count() == 0


# ---------------------------------------------------------------------------
# Scenario 6: Users row — label navigates, chevron does not
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUsersRowSplitInteraction:
    """The Users row label is a navigating <a>; the chevron is a
    separate <button> that toggles the sub-group without navigating."""

    def test_users_row_label_and_chevron_split(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Expand People so the Users row is visible.
        _section_button(page, "people").click()

        chevron = page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        )
        children = page.locator("#studio-users-children")

        # Initial state on /studio/: chevron collapsed, children hidden.
        assert chevron.get_attribute("aria-expanded") == "false"
        assert not children.is_visible()

        # Click the chevron — page URL must stay on /studio/.
        url_before = page.url
        chevron.click()
        assert page.url == url_before
        assert chevron.get_attribute("aria-expanded") == "true"
        assert children.is_visible()

        # Click again to collapse.
        chevron.click()
        assert page.url == url_before
        assert chevron.get_attribute("aria-expanded") == "false"
        assert not children.is_visible()

        # Now click the Users label (the <a>) — must navigate.
        page.locator(
            '#studio-section-people a[href="/studio/users/"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/users/" in page.url


# ---------------------------------------------------------------------------
# Scenario 7: theme toggle works from its new top-of-sidebar position
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestThemeToggleAtTop:
    """The theme toggle still works after moving to the top utility row."""

    def test_theme_toggle_flips_theme_and_persists(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        toggle = page.locator('aside#studio-sidebar [data-testid="theme-toggle"]')
        assert toggle.count() == 1

        # Theme toggle is positioned above the first section group.
        toggle_y = toggle.bounding_box()["y"]
        first_section_y = _section_button(page, "content").bounding_box()["y"]
        assert toggle_y < first_section_y

        # Read initial theme, click toggle, confirm it changed.
        initial = page.evaluate(
            "document.documentElement.classList.contains('dark') ? 'dark' : 'light'"
        )
        toggle.click()
        page.wait_for_function(
            "(initial) => {"
            "  const now = document.documentElement.classList.contains('dark') ? 'dark' : 'light';"
            "  return now !== initial;"
            "}",
            arg=initial,
        )
        after = page.evaluate(
            "document.documentElement.classList.contains('dark') ? 'dark' : 'light'"
        )
        assert after != initial

        # Reload — the theme toggle still sits at the top of the sidebar.
        page.reload(wait_until="domcontentloaded")
        toggle_after = page.locator(
            'aside#studio-sidebar [data-testid="theme-toggle"]'
        )
        assert toggle_after.bounding_box()["y"] < _section_button(
            page, "content"
        ).bounding_box()["y"]


# ---------------------------------------------------------------------------
# Scenario 8: Back-to-website link
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestBackToWebsite:
    """The top-of-sidebar Back-to-website link points at /."""

    def test_back_to_website_navigates_to_root(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/campaigns/", wait_until="domcontentloaded"
        )

        back = page.locator(
            'aside#studio-sidebar a[href="/"]:has(span:text-is("Back to website"))'
        )
        assert back.count() == 1
        back.click()
        page.wait_for_load_state("domcontentloaded")
        # Some installs redirect ``/`` to ``/dashboard/`` for logged-in users.
        # The acceptance criterion is that we leave /studio/.
        assert "/studio/" not in page.url


# ---------------------------------------------------------------------------
# Scenario 9: renamed labels visible to operators
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestRenamedLabels:
    """Operators see the new labels (Email campaigns, Site banner,
    UTM links, UTM analytics, Operations) and never the old ones."""

    def test_marketing_labels_match_spec(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Expand Marketing.
        _section_button(page, "marketing").click()

        # Inside the Marketing section, the visible link labels are
        # exactly the five renamed items.
        marketing_labels = [
            t.strip()
            for t in page.locator(
                "#studio-section-marketing a span"
            ).all_inner_texts()
            if t.strip()
        ]
        assert marketing_labels == [
            "Email campaigns",
            "Email templates",
            "Site banner",
            "UTM links",
            "UTM analytics",
        ], marketing_labels

        # Old labels must not appear as <span> link labels anywhere
        # in the sidebar. We use ``text-is`` for an exact match (it is
        # stricter than ``has-text``'s case-insensitive substring).
        for old in ["Campaigns", "UTM Campaigns", "Announcement"]:
            assert page.locator(
                f'aside#studio-sidebar a span:text-is("{old}")'
            ).count() == 0, f"old label still rendered: {old!r}"

    def test_operations_section_label_replaces_system(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        ops_button = _section_button(page, "operations")
        # Header text reads "Operations", not "System".
        assert ops_button.locator('span:text-is("Operations")').count() == 1
        assert page.locator(
            'aside#studio-sidebar span:text-is("System")'
        ).count() == 0


# ---------------------------------------------------------------------------
# Scenario 10: mobile drawer continues to work after the reorg
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMobileDrawerAfterReorg:
    """Section-header taps don't close the open mobile drawer; <a> taps
    still do."""

    def test_section_header_keeps_drawer_open_link_closes_it(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        page.locator("#studio-sidebar-toggle").click()
        sidebar = page.locator("aside#studio-sidebar")
        # Use whole-token matching: the class list always contains
        # substrings like ``overflow-hidden`` and ``md:hidden``. Only the
        # bare ``hidden`` class controls drawer visibility.
        assert not _has_class(sidebar.get_attribute("class"), "hidden")

        # Tap the People section header — the drawer must stay open.
        _section_button(page, "people").click()
        assert not _has_class(sidebar.get_attribute("class"), "hidden")
        assert _section_list(page, "people").is_visible()

        # Tap the CRM link inside the now-expanded People section.
        page.locator(
            '#studio-section-people a[href="/studio/crm/"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/crm/" in page.url

        # And the drawer is closed after the link click.
        sidebar_after = page.locator("aside#studio-sidebar")
        assert _has_class(sidebar_after.get_attribute("class"), "hidden")


# ---------------------------------------------------------------------------
# Scenario 11: keyboard toggling a section
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestKeyboardSectionToggle:
    """Section toggle buttons work with both Enter and Space."""

    def test_enter_and_space_toggle_section(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        events_button = _section_button(page, "events")
        events_ul = _section_list(page, "events")

        # Focus the button and press Enter to expand.
        events_button.focus()
        assert events_button.get_attribute("aria-expanded") == "false"
        page.keyboard.press("Enter")
        assert events_button.get_attribute("aria-expanded") == "true"
        assert events_ul.is_visible()
        # The three children are in the tab order (visible + focusable).
        for label in ["Events", "Event groups", "Notifications"]:
            link = page.locator(
                f'#studio-section-events a:has(span:text-is("{label}"))'
            )
            assert link.count() >= 1
            assert link.first.is_visible()

        # Press Space to collapse.
        events_button.focus()
        page.keyboard.press("Space")
        assert events_button.get_attribute("aria-expanded") == "false"
        assert not events_ul.is_visible()
