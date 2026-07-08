"""Playwright coverage for the responsive Studio event roster (#1144).

The Studio event edit page "Registered attendees" panel previously
rendered a single ``table-fixed w-full`` table that clipped names/emails
and overlapped the Registered/Tier/Joined columns on a phone. This suite
verifies the desktop table + mobile stacked-card responsive pattern:
full name + email legible on mobile with no horizontal overflow, the
overlapping-columns bug gone, the desktop table unchanged with no
duplicate rows, the email filter working identically at both viewports,
Download CSV reachable on mobile, and a clean empty state on mobile.
"""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 720}

LONG_NAME = "Aleksandrina Konstantinopolskaya-Wintersteinberg"
LONG_EMAIL = "aleksandrina.konstantinopolskaya.wintersteinberg@really-long-domain-example.com"


def _reset():
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(*, slug="roster-1144", title="Roster 1144"):
    from events.models import Event

    now = timezone.now()
    event = Event.objects.create(
        slug=slug,
        title=title,
        description=f"{title} description.",
        start_datetime=now + datetime.timedelta(hours=1),
        end_datetime=now + datetime.timedelta(hours=2),
        status="upcoming",
        published=True,
    )
    connection.close()
    return event


def _named_user(email, first, last, tier_slug="free"):
    user = _create_user(email, tier_slug=tier_slug)
    user.first_name = first
    user.last_name = last
    user.save(update_fields=["first_name", "last_name"])
    connection.close()
    return user


def _register(user, event, *, joined=False):
    from events.models import EventRegistration

    reg = EventRegistration.objects.create(user=user, event=event)
    if joined:
        reg.joined_at = timezone.now()
        reg.save(update_fields=["joined_at"])
    connection.close()
    return reg


def _edit_url(server, event):
    return f"{server}/studio/events/{event.pk}/edit"


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestStudioEventRosterMobile1144:
    def test_full_name_and_email_legible_on_mobile_no_overflow(
        self, django_server, browser,
    ):
        """Scenario: operator reads the full roster on their phone."""
        _reset()
        _create_staff_user("admin-1144a@test.com")
        event = _create_event(slug="roster-1144a", title="Roster 1144a")
        attendee = _named_user(
            LONG_EMAIL, "Aleksandrina", "Konstantinopolskaya-Wintersteinberg",
            tier_slug="premium",
        )
        _register(attendee, event, joined=True)
        _register(_named_user("short-1144a@test.com", "Sam", "Lee"), event)

        context = _auth_context(browser, "admin-1144a@test.com")
        page = context.new_page()
        page.set_viewport_size(MOBILE)
        page.goto(_edit_url(django_server, event), wait_until="domcontentloaded")

        panel = page.locator('[data-testid="event-registrations-panel"]')
        panel.scroll_into_view_if_needed()

        # Mobile cards visible, desktop table hidden.
        cards = page.locator('[data-testid="registration-card"]')
        expect(cards.first).to_be_visible()
        assert cards.count() == 2
        expect(page.locator('[data-testid="registrations-table-wrapper"]')).to_be_hidden()

        # The long-named attendee's FULL name and FULL email are present,
        # not clipped to an ellipsis / leading substring.
        card = page.locator(
            f'[data-testid="registration-card"][data-email="{LONG_EMAIL.lower()}"]'
        )
        expect(card).to_be_visible()
        assert LONG_NAME in card.inner_text()
        assert LONG_EMAIL in card.inner_text()

        # Registered / Tier / Joined each on their own labeled row.
        card_text = card.inner_text()
        assert "Registered" in card_text
        assert "Tier" in card_text
        assert "Premium" in card_text
        assert "Joined" in card_text

        # No horizontal overflow of the panel — cannot need sideways scroll.
        overflow = panel.evaluate(
            "el => el.scrollWidth > el.clientWidth"
        )
        assert overflow is False, "Registrations panel overflows horizontally on mobile"

        # And the card itself does not overflow (name/email wrapped, not clipped).
        card_overflow = card.evaluate("el => el.scrollWidth > el.clientWidth")
        assert card_overflow is False, "Attendee card overflows horizontally on mobile"

        context.close()

    def test_overlapping_columns_bug_is_gone_on_mobile(
        self, django_server, browser,
    ):
        """Scenario: the REGISTEREDTIER / fused-values garbage is gone."""
        _reset()
        _create_staff_user("admin-1144b@test.com")
        event = _create_event(slug="roster-1144b", title="Roster 1144b")
        joined_user = _named_user("joined-1144b@test.com", "Nina", "Park", tier_slug="premium")
        _register(joined_user, event, joined=True)
        _register(_named_user("notjoined-1144b@test.com", "Omar", "Diaz", tier_slug="main"), event)

        context = _auth_context(browser, "admin-1144b@test.com")
        page = context.new_page()
        page.set_viewport_size(MOBILE)
        page.goto(_edit_url(django_server, event), wait_until="domcontentloaded")

        card = page.locator(
            '[data-testid="registration-card"][data-email="joined-1144b@test.com"]'
        )
        expect(card).to_be_visible()

        # Distinct, separately readable values: the green Joined badge is its
        # own element and the Tier value is its own <dd>.
        expect(card.locator('[data-testid="registration-card-joined-badge"]')).to_have_text(
            "Joined"
        )
        # Tier row renders "Premium" without a date fused onto it.
        dds = card.locator("dd")
        tier_dd_text = dds.nth(1).inner_text().strip()
        assert tier_dd_text == "Premium", f"Tier value fused/garbled: {tier_dd_text!r}"

        # Labels and values are separated, so the fused-columns garbage from
        # the bug never appears as an unbroken token.
        collapsed = "".join(card.inner_text().split())
        assert "RegisteredTier" not in collapsed
        assert "TierJoined" not in collapsed
        assert "REGISTEREDTIER" not in collapsed.upper()

        context.close()

    def test_desktop_table_unchanged_no_duplicate_rows(
        self, django_server, browser,
    ):
        """Scenario: desktop table is unchanged for a laptop operator."""
        _reset()
        _create_staff_user("admin-1144c@test.com")
        event = _create_event(slug="roster-1144c", title="Roster 1144c")
        _register(_named_user("a-1144c@test.com", "Ann", "Blake"), event)
        _register(_named_user("b-1144c@test.com", "Bob", "Cole", tier_slug="main"), event, joined=True)

        context = _auth_context(browser, "admin-1144c@test.com")
        page = context.new_page()
        page.set_viewport_size(DESKTOP)
        page.goto(_edit_url(django_server, event), wait_until="domcontentloaded")

        table = page.locator('[data-testid="registrations-table"]')
        expect(table).to_be_visible()

        # 5-column header present.
        headers = page.locator('[data-testid="registrations-table"] thead th')
        assert headers.count() == 5
        # Header labels are rendered uppercase via the CSS ``uppercase`` class,
        # so inner_text() returns them uppercased.
        assert [headers.nth(i).inner_text().strip().lower() for i in range(5)] == [
            "name", "email", "registered", "tier", "joined",
        ]

        # Mobile cards not visible on desktop.
        expect(page.locator('[data-testid="registrations-cards"]')).to_be_hidden()

        # Each attendee appears exactly once in visible rows (no leaked cards).
        rows = page.locator('[data-testid="registration-row"]')
        assert rows.count() == 2
        for email in ("a-1144c@test.com", "b-1144c@test.com"):
            visible_rows = page.locator(
                f'[data-testid="registration-row"][data-email="{email}"]'
            )
            assert visible_rows.count() == 1

        context.close()

    def test_filter_by_email_works_at_mobile_and_desktop(
        self, django_server, browser,
    ):
        """Scenario: filtering by email works identically at both viewports."""
        _reset()
        _create_staff_user("admin-1144d@test.com")
        event = _create_event(slug="roster-1144d", title="Roster 1144d")
        _register(_named_user("alice-1144d@test.com", "Alice", "One"), event)
        _register(_named_user("bob-1144d@test.com", "Bob", "Two", tier_slug="main"), event)
        _register(_named_user("carol-1144d@test.com", "Carol", "Three", tier_slug="premium"), event)

        context = _auth_context(browser, "admin-1144d@test.com")
        page = context.new_page()

        # Mobile: cards filtered.
        page.set_viewport_size(MOBILE)
        page.goto(_edit_url(django_server, event), wait_until="domcontentloaded")
        filter_input = page.locator('[data-testid="registrations-filter"]')
        filter_input.fill("alice-1144d")

        alice_card = page.locator(
            '[data-testid="registration-card"][data-email="alice-1144d@test.com"]'
        )
        bob_card = page.locator(
            '[data-testid="registration-card"][data-email="bob-1144d@test.com"]'
        )
        expect(alice_card).to_be_visible()
        expect(bob_card).to_be_hidden()

        # Clear and switch to desktop: rows filtered by same fragment.
        filter_input.fill("")
        page.set_viewport_size(DESKTOP)
        filter_input.fill("alice-1144d")

        alice_row = page.locator(
            '[data-testid="registration-row"][data-email="alice-1144d@test.com"]'
        )
        bob_row = page.locator(
            '[data-testid="registration-row"][data-email="bob-1144d@test.com"]'
        )
        expect(alice_row).to_be_visible()
        expect(bob_row).to_be_hidden()

        context.close()

    def test_download_csv_reachable_on_mobile(self, django_server, browser):
        """Scenario: operator exports the roster from their phone."""
        _reset()
        _create_staff_user("admin-1144e@test.com")
        event = _create_event(slug="roster-1144e", title="Roster 1144e")
        _register(_named_user("e-1144e@test.com", "Eve", "Ng"), event)

        context = _auth_context(browser, "admin-1144e@test.com")
        page = context.new_page()
        page.set_viewport_size(MOBILE)
        page.goto(_edit_url(django_server, event), wait_until="domcontentloaded")

        csv = page.locator('[data-testid="registrations-download-csv"]')
        expect(csv).to_be_visible()
        href = csv.get_attribute("href")
        assert href and f"/studio/events/{event.pk}/registrations.csv" in href

        # The control renders with a real tappable box (visible, not clipped);
        # its padding/height is the existing header chrome, unchanged by #1144.
        box = csv.bounding_box()
        assert box is not None and box["height"] > 0 and box["width"] > 0

        context.close()

    def test_empty_state_clean_on_mobile(self, django_server, browser):
        """Scenario: event with no registrations shows a clean empty state."""
        _reset()
        _create_staff_user("admin-1144f@test.com")
        event = _create_event(slug="roster-1144f", title="Roster 1144f")

        context = _auth_context(browser, "admin-1144f@test.com")
        page = context.new_page()
        page.set_viewport_size(MOBILE)
        page.goto(_edit_url(django_server, event), wait_until="domcontentloaded")

        panel = page.locator('[data-testid="event-registrations-panel"]')
        panel.scroll_into_view_if_needed()
        expect(page.locator('[data-testid="registrations-empty"]')).to_have_text(
            "No registrations yet."
        )
        # No table, no cards, no horizontal overflow.
        expect(page.locator('[data-testid="registrations-cards"]')).to_have_count(0)
        expect(page.locator('[data-testid="registrations-table"]')).to_have_count(0)
        overflow = panel.evaluate("el => el.scrollWidth > el.clientWidth")
        assert overflow is False

        context.close()
