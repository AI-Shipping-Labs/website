"""End-to-end tests for the reorganised Studio sidebar (issues #570, #576).

The Studio sidebar is split into a small top utility row
(``Back to website`` + theme toggle), a Dashboard link, and six
collapsible sections — Events, Content, People, Planning, Marketing,
Operations.
The section that contains the current page auto-expands server-side; on
the dashboard only Events is open (#576 moved Events to the top and
flipped the dashboard default from Content to Events).

Coverage mirrors the Playwright scenarios in the issue bodies. Per the
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
        first_section = _section_button(page, "events")

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

        # Section toggles render in the order: Events, Content, People,
        # Planning, Marketing, Operations. Bounding-box y-positions are strictly
        # increasing.
        section_ys = [
            _section_button(page, slug).bounding_box()["y"]
            for slug in (
                "events",
                "content",
                "people",
                "planning",
                "marketing",
                "operations",
            )
        ]
        assert section_ys == sorted(section_ys), (
            f"Sections out of order; y-positions: {section_ys}"
        )

        # Events is expanded — its three child links are visible.
        for label in ["Events", "Event series", "Notifications"]:
            link = page.locator(
                f'#studio-section-events a:has(span:text-is("{label}"))'
            )
            assert link.count() == 1
            assert link.is_visible(), f"{label!r} should be visible in expanded Events"

        # The other five sections are collapsed — their <ul> bodies are not visible.
        for slug in ("content", "people", "planning", "marketing", "operations"):
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

        for label in ["Users", "Imports", "Tier overrides", "CRM"]:
            assert page.locator(
                f'#studio-section-people a:has(span:text-is("{label}"))'
            ).count() >= 1
        assert page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        ).count() == 0

        # Active highlight on the CRM link.
        crm_link = page.locator(
            '#studio-section-people a[href="/studio/crm/"]'
        )
        assert "bg-secondary" in (crm_link.get_attribute("class") or "")

        # All other sections collapsed — Events also collapses now that
        # another section is active (#576).
        for slug in ("content", "events", "planning", "marketing", "operations"):
            assert not _section_list(page, slug).is_visible()


# ---------------------------------------------------------------------------
# Scenario 4: deep-link into Imports — People section auto-expands.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestImportsAutoExpandsPeople:
    """Navigating to /studio/imports/ expands the flat People section."""

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
        assert page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        ).count() == 0
        assert page.locator("#studio-users-children").count() == 0

        imports_link = page.locator(
            '#studio-section-people a[href="/studio/imports/"]'
        )
        assert "bg-secondary" in (imports_link.get_attribute("class") or "")

        # New user link is rendered (superuser only).
        new_user = page.locator(
            '#studio-section-people a[href="/studio/users/new/"]'
        )
        assert new_user.count() == 1
        assert new_user.is_visible()


# ---------------------------------------------------------------------------
# Scenario 5: non-superuser cannot see superuser-only items
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestNonSuperuserGating:
    """Non-superuser staff do not see ``New user`` or ``API tokens``."""

    def test_people_section_omits_new_user_for_non_superuser(
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

        assert page.locator("#studio-users-children").count() == 0

        # Imports + Tier overrides visible; New user not rendered at all.
        assert page.locator(
            '#studio-section-people a[href="/studio/imports/"]'
        ).count() == 1
        assert page.locator(
            '#studio-section-people a[href="/studio/tier_overrides/"]'
        ).count() == 1
        assert page.locator(
            '#studio-section-people a[href="/studio/users/new/"]'
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
# Scenario 6: Users row — single anchor, no inner subgroup
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUsersRowIsSingleAnchor:
    """Users is a plain link and People has no nested Users subgroup."""

    def test_users_row_is_a_single_link_in_flat_people_section(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Expand People so the Users row is visible.
        _section_button(page, "people").click()

        # The chevron sub-button is gone (#624).
        assert page.locator(
            'aside#studio-sidebar [data-studio-users-toggle]'
        ).count() == 0

        assert page.locator("#studio-users-children").count() == 0
        for label in ["Imports", "Tier overrides"]:
            assert page.locator(
                f'#studio-section-people a:has(span:text-is("{label}"))'
            ).is_visible()

        # The Users row itself is exactly one <a> pointing at /studio/users/.
        users_link = page.locator(
            '#studio-section-people > li > a[href="/studio/users/"]'
        )
        assert users_link.count() == 1

        # Clicking the Users link navigates to /studio/users/.
        users_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/users/" in page.url


# ---------------------------------------------------------------------------
# Scenario 7: Planning section
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPlanningSection:
    """Sprints and Plans live in their own top-level Planning section."""

    def test_sprints_deep_link_expands_planning_and_highlights_sprints(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/sprints/", wait_until="domcontentloaded"
        )

        planning_button = _section_button(page, "planning")
        assert planning_button.get_attribute("aria-expanded") == "true"
        assert _section_list(page, "planning").is_visible()

        sprints_link = page.locator(
            '#studio-section-planning a[href="/studio/sprints/"]'
        )
        assert "bg-secondary" in (sprints_link.get_attribute("class") or "")

        for slug in ("content", "events", "people", "marketing", "operations"):
            assert not _section_list(page, slug).is_visible()

        people_y = _section_button(page, "people").bounding_box()["y"]
        planning_y = planning_button.bounding_box()["y"]
        marketing_y = _section_button(page, "marketing").bounding_box()["y"]
        assert people_y < planning_y < marketing_y

    def test_plans_deep_link_expands_planning_and_highlights_plans(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/plans/", wait_until="domcontentloaded"
        )

        assert _section_button(page, "planning").get_attribute(
            "aria-expanded"
        ) == "true"
        assert _section_list(page, "planning").is_visible()

        plans_link = page.locator(
            '#studio-section-planning a[href="/studio/plans/"]'
        )
        assert "bg-secondary" in (plans_link.get_attribute("class") or "")
        assert not _section_list(page, "people").is_visible()

    def test_dashboard_shows_planning_between_people_and_marketing(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        planning = _section_button(page, "planning")
        assert planning.get_attribute("aria-expanded") == "false"
        assert not _section_list(page, "planning").is_visible()

        people_y = _section_button(page, "people").bounding_box()["y"]
        planning_y = planning.bounding_box()["y"]
        marketing_y = _section_button(page, "marketing").bounding_box()["y"]
        assert people_y < planning_y < marketing_y

        _section_button(page, "people").click()
        people_labels = page.locator("#studio-section-people a span").all_inner_texts()
        assert [label.strip() for label in people_labels if label.strip()] == [
            "Users",
            "Imports",
            "Tier overrides",
            "New user",
            "CRM",
        ]

        planning.click()
        planning_labels = page.locator(
            "#studio-section-planning a span"
        ).all_inner_texts()
        assert [label.strip() for label in planning_labels if label.strip()] == [
            "Sprints",
            "Plans",
        ]


# ---------------------------------------------------------------------------
# Scenario 8: theme toggle works from its new top-of-sidebar position
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
        first_section_y = _section_button(page, "events").bounding_box()["y"]
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
            page, "events"
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

        # Use Content for keyboard toggling — Events is the dashboard
        # default and starts expanded after #576, so a "press Enter to
        # expand" test against it would assert the wrong initial state.
        content_button = _section_button(page, "content")
        content_ul = _section_list(page, "content")

        # Focus the button and press Enter to expand.
        content_button.focus()
        assert content_button.get_attribute("aria-expanded") == "false"
        page.keyboard.press("Enter")
        assert content_button.get_attribute("aria-expanded") == "true"
        assert content_ul.is_visible()
        # The six children are in the tab order (visible + focusable).
        for label in ["Articles", "Courses", "Projects", "Workshops", "Recordings", "Downloads"]:
            link = page.locator(
                f'#studio-section-content a:has(span:text-is("{label}"))'
            )
            assert link.count() >= 1
            assert link.first.is_visible()

        # Press Space to collapse.
        content_button.focus()
        page.keyboard.press("Space")
        assert content_button.get_attribute("aria-expanded") == "false"
        assert not content_ul.is_visible()


# ---------------------------------------------------------------------------
# #576 Scenario: admin uses Events as their default landing surface
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEventsAsDashboardDefault:
    """The Events section opens by default on /studio/ and the admin
    can click straight through to /studio/events/."""

    def test_dashboard_opens_to_events_then_navigates(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        events_button = _section_button(page, "events")
        events_ul = _section_list(page, "events")
        assert events_button.get_attribute("aria-expanded") == "true"
        assert events_ul.is_visible()

        # Click the Events link inside the open Events section.
        page.locator(
            '#studio-section-events a[href="/studio/events/"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/events/" in page.url

        # Events section is still expanded on /studio/events/.
        assert _section_button(page, "events").get_attribute(
            "aria-expanded"
        ) == "true"
        assert _section_list(page, "events").is_visible()

        # Return to /studio/; Events is again the only open section.
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        assert _section_button(page, "events").get_attribute(
            "aria-expanded"
        ) == "true"
        for slug in ("content", "people", "planning", "marketing", "operations"):
            assert _section_button(page, slug).get_attribute(
                "aria-expanded"
            ) == "false"
            assert not _section_list(page, slug).is_visible()


# ---------------------------------------------------------------------------
# #576 Scenario: visiting Content auto-expands Content and collapses Events
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestContentPageCollapsesEvents:
    """Deep-linking to a Content page expands Content and pulls Events
    closed — Events only stays open on the dashboard or its own pages."""

    def test_articles_page_expands_content_and_collapses_events(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/articles/", wait_until="domcontentloaded"
        )

        # Content is expanded; Articles is reachable without clicking.
        content_button = _section_button(page, "content")
        assert content_button.get_attribute("aria-expanded") == "true"
        assert page.locator(
            '#studio-section-content a:has(span:text-is("Articles"))'
        ).is_visible()

        # Events is now collapsed — its <ul> has the hidden class and the
        # Events link inside it is not visible until the toggle is clicked.
        events_button = _section_button(page, "events")
        assert events_button.get_attribute("aria-expanded") == "false"
        assert not _section_list(page, "events").is_visible()
        assert not page.locator(
            '#studio-section-events a[href="/studio/events/"]'
        ).is_visible()

        # People, Planning, Marketing, Operations are also collapsed.
        for slug in ("people", "planning", "marketing", "operations"):
            assert not _section_list(page, slug).is_visible()


# ---------------------------------------------------------------------------
# #576 Scenario: visiting an Events page keeps the Events section expanded
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEventsPageKeepsEventsExpanded:
    """When the active page is inside the Events section, Events stays
    expanded and the Event series link is highlighted."""

    def test_event_series_page_keeps_events_open(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(
            f"{django_server}/studio/event-series/",
            wait_until="domcontentloaded",
        )

        events_button = _section_button(page, "events")
        assert events_button.get_attribute("aria-expanded") == "true"
        assert _section_list(page, "events").is_visible()

        event_series_link = page.locator(
            '#studio-section-events a[href="/studio/event-series/"]'
        )
        assert event_series_link.count() == 1
        assert "bg-secondary" in (
            event_series_link.get_attribute("class") or ""
        )

        for slug in ("content", "people", "planning", "marketing", "operations"):
            assert not _section_list(page, slug).is_visible()


# ---------------------------------------------------------------------------
# #576 Scenario: sections are independent toggles
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSectionsAreIndependentTogglesAfterReorder:
    """Opening Content while Events is already expanded does not collapse
    Events; collapsing Events leaves Content open."""

    def test_open_content_then_collapse_events(
        self, django_server, browser
    ):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        events_button = _section_button(page, "events")
        content_button = _section_button(page, "content")
        assert events_button.get_attribute("aria-expanded") == "true"
        assert content_button.get_attribute("aria-expanded") == "false"

        # Click Content header — Content opens, Events stays open.
        content_button.click()
        assert content_button.get_attribute("aria-expanded") == "true"
        assert _section_list(page, "content").is_visible()
        assert page.locator(
            '#studio-section-content a:has(span:text-is("Articles"))'
        ).is_visible()
        assert events_button.get_attribute("aria-expanded") == "true"
        assert _section_list(page, "events").is_visible()

        # Click Events header — Events collapses, Content stays open.
        events_button.click()
        assert events_button.get_attribute("aria-expanded") == "false"
        assert not _section_list(page, "events").is_visible()
        assert content_button.get_attribute("aria-expanded") == "true"
        assert _section_list(page, "content").is_visible()


# ---------------------------------------------------------------------------
# #576 Scenario: Event series link still reachable via its preserved test hook
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEventSeriesTestidPreserved:
    """The ``data-testid="sidebar-event-series-link"`` hook still resolves
    to a single link inside the Events section and still navigates."""

    def test_event_series_testid_navigates(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        link = page.locator('[data-testid="sidebar-event-series-link"]')
        assert link.count() == 1
        # Lives inside the Events section.
        events_link = page.locator(
            '#studio-section-events [data-testid="sidebar-event-series-link"]'
        )
        assert events_link.count() == 1
        assert events_link.get_attribute("href") == "/studio/event-series/"
        assert "Event series" in (events_link.inner_text() or "")

        events_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/event-series/" in page.url
        assert _section_button(page, "events").get_attribute(
            "aria-expanded"
        ) == "true"
