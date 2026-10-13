"""Full-width event roster and the "Not raising access" filter (issue #1936).

On the Studio event edit page the Registered attendees and Feedback panels
span both grid columns below the form and side panel at lg and up, so the
roster table never scrolls sideways; below lg the roster is a card list.
``/studio/tier_overrides/`` lets the operator find grants that raise nobody's
access (at or below the stored tier) and revoke them without losing the
filter.
"""

import datetime
import os
import re

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only

STAFF_EMAIL = "studio-roster-1936@test.com"
MEMBER_EMAIL = "main@test.com"
STACKED_EMAIL = "stacked-1936@test.com"
FREE_EMAIL = "free-attendee-1936@test.com"
JOINED_EMAIL = "joined-attendee-1936@test.com"
LONG_EMAIL = (
    "averyveryveryverylongfirstname.lastname.1936@example-company-mail.com"
)
EVENT_SLUG = "roster-width-1936"

A_EMAIL = "leftover-main-1936@test.com"
B_EMAIL = "comped-free-1936@test.com"
C_EMAIL = "equal-basic-1936@test.com"
D_EMAIL = "upgraded-premium-1936@test.com"

LAPTOP_VIEWPORTS = (
    {"width": 1024, "height": 768},
    {"width": 1280, "height": 800},
)
TABLET_VIEWPORT = {"width": 800, "height": 900}
PHONE_VIEWPORT = {"width": 390, "height": 844}


def _reset():
    from events.models import Event
    from payments.models import TierOverride

    TierOverride.objects.all().delete()
    Event.objects.filter(slug=EVENT_SLUG).delete()
    connection.close()


def _grant(email, tier_slug, days, *, original_tier_slug=None, source=""):
    from accounts.models import User
    from payments.models import Tier, TierOverride

    override = TierOverride.objects.create(
        user=User.objects.get(email=email),
        override_tier=Tier.objects.get(slug=tier_slug),
        original_tier=(
            Tier.objects.get(slug=original_tier_slug)
            if original_tier_slug else None
        ),
        expires_at=timezone.now() + datetime.timedelta(days=days),
        source=source,
    )
    connection.close()
    return override


def _set_stored_tier(email, tier_slug):
    from accounts.models import User
    from payments.models import Tier
    from tests.fixtures import set_membership

    set_membership(
        User.objects.get(email=email), tier=Tier.objects.get(slug=tier_slug),
    )
    connection.close()


def _user_pk(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _is_active(override_pk):
    from payments.models import TierOverride

    active = TierOverride.objects.get(pk=override_pk).is_active
    connection.close()
    return active


def _seed_override_fixture():
    """A Main + Basic grant, B Free + Premium and Basic grants, C Basic +
    Basic grant, D granted Main while Free but now stored Premium."""
    _create_user(A_EMAIL, tier_slug="main", first_name="Ada")
    _create_user(B_EMAIL, tier_slug="free", first_name="Ben")
    _create_user(C_EMAIL, tier_slug="basic", first_name="Cleo")
    _create_user(D_EMAIL, tier_slug="premium", first_name="Dov")
    return {
        "a_basic": _grant(A_EMAIL, "basic", 5, original_tier_slug="main"),
        "b_premium": _grant(B_EMAIL, "premium", 10, original_tier_slug="free"),
        "c_basic": _grant(C_EMAIL, "basic", 20, original_tier_slug="basic"),
        "d_main": _grant(D_EMAIL, "main", 30, original_tier_slug="free"),
        "b_basic": _grant(
            B_EMAIL, "basic", 300, original_tier_slug="free",
            source="maven:ai-buildcamp-1936",
        ),
    }


def _seed_roster_event(*, feedback=False):
    from accounts.models import User
    from events.models import Event, EventFeedback, EventRegistration

    stacked = _create_user(STACKED_EMAIL, first_name="Stacked")
    _grant(STACKED_EMAIL, "premium", 20)
    _grant(STACKED_EMAIL, "basic", 300, source="maven:ai-buildcamp-1936")
    free = _create_user(FREE_EMAIL, first_name="Fran")
    long_member = _create_user(LONG_EMAIL)
    User.objects.filter(pk=long_member.pk).update(
        first_name="Bartholomew-Alexandrina",
        last_name="Featherstonehaugh-Montgomery",
    )
    joined = _create_user(JOINED_EMAIL, first_name="Joan")
    event = Event.objects.create(
        title="Roster width 1936", slug=EVENT_SLUG, status="completed",
        start_datetime=timezone.now() - datetime.timedelta(days=2),
    )
    registered_at = datetime.datetime(
        2026, 10, 9, 13, 9, tzinfo=datetime.timezone.utc,
    )
    for user in (free, long_member, joined, stacked):
        registration = EventRegistration.objects.create(event=event, user=user)
        EventRegistration.objects.filter(pk=registration.pk).update(
            registered_at=registered_at,
            joined_at=registered_at if user == joined else None,
        )
    if feedback:
        EventFeedback.objects.create(
            event=event, user=joined, rating=5,
            comment="The live debugging part was the most useful.",
        )
        EventFeedback.objects.create(
            event=event, user=free, rating=3,
            comment="Slides were hard to read on a laptop.",
        )
    connection.close()
    return event


def _open_edit(page, base_url, event, viewport):
    page.set_viewport_size(viewport)
    page.goto(
        f"{base_url}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )


_PAGE_SCROLLS_SIDEWAYS = (
    "() => document.documentElement.scrollWidth > window.innerWidth"
)

# Geometry of a full-width panel relative to the main column and the aside.
_PANEL_GEOMETRY = """
(testid) => {
  const rect = (el) => el.getBoundingClientRect();
  const panel = rect(document.querySelector(`[data-testid="${testid}"]`));
  const mainColumn = rect(document.getElementById('event-edit-form').parentElement);
  const aside = rect(document.querySelector('[data-testid="studio-meta-actions-panel"]'));
  return {
    leftDelta: Math.abs(panel.left - mainColumn.left),
    rightDelta: Math.abs(panel.right - aside.right),
    gapBelowMain: panel.top - mainColumn.bottom,
    gapBelowAside: panel.top - aside.bottom,
  };
}
"""

# Every cell keeps its content inside its own box, Registered never reaches
# into Tier, and every header sits inside the wrapper's visible area.
_TABLE_REPORT = """
(wrapper) => {
  const box = wrapper.getBoundingClientRect();
  const headersInside = Array.from(wrapper.querySelectorAll('thead th')).every(
    (th) => {
      const r = th.getBoundingClientRect();
      return r.left >= box.left - 1 && r.right <= box.right + 1;
    });
  const rows = Array.from(wrapper.querySelectorAll('tbody tr')).map((row) => {
    const cells = Array.from(row.querySelectorAll('td'));
    const range = document.createRange();
    range.selectNodeContents(cells[2]);
    return {
      overflowing: cells.filter((td) => td.scrollWidth > td.clientWidth).length,
      registeredIntoTier:
        range.getBoundingClientRect().right > cells[3].getBoundingClientRect().left,
    };
  });
  return {
    scrollsSideways: wrapper.scrollWidth > wrapper.clientWidth,
    headersInside,
    rows,
  };
}
"""


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_laptop_roster_spans_full_width_with_every_column_visible(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    event = _seed_roster_event()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    for viewport in LAPTOP_VIEWPORTS:
        _open_edit(page, django_server, event, viewport)
        page.get_by_test_id("event-registrations-panel").scroll_into_view_if_needed()
        table = page.get_by_test_id("registrations-table")
        expect(table).to_be_visible()
        expect(page.get_by_test_id("registrations-cards")).to_be_hidden()
        assert [
            text.strip() for text in table.locator("thead th").all_text_contents()
        ] == ["Name", "Email", "Registered", "Tier", "Joined"]

        report = page.get_by_test_id("registrations-table-wrapper").evaluate(
            _TABLE_REPORT,
        )
        assert report == {
            "scrollsSideways": False,
            "headersInside": True,
            "rows": [{"overflowing": 0, "registeredIntoTier": False}] * 4,
        }, (viewport, report)

        geometry = page.evaluate(_PANEL_GEOMETRY, "event-registrations-panel")
        assert geometry["leftDelta"] <= 2, (viewport, geometry)
        assert geometry["rightDelta"] <= 2, (viewport, geometry)
        assert geometry["gapBelowMain"] >= 0, (viewport, geometry)
        assert geometry["gapBelowAside"] >= 0, (viewport, geometry)
        assert not page.evaluate(_PAGE_SCROLLS_SIDEWAYS), viewport

        rows = page.get_by_test_id("registration-row")
        stacked_tier = rows.filter(has_text=STACKED_EMAIL).get_by_test_id(
            "registration-tier",
        )
        expect(stacked_tier).to_have_text(re.compile(r"^\s*Premium \(override until "))
        joined_row = rows.filter(has_text=JOINED_EMAIL)
        expect(joined_row.get_by_test_id("registration-joined-badge")).to_be_visible()
        expect(joined_row.get_by_test_id("registration-joined-at")).to_have_text(
            "2026-10-09 13:09",
        )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_operator_filters_laptop_roster_to_one_attendee_and_opens_them(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    event = _seed_roster_event()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    for viewport in LAPTOP_VIEWPORTS:
        _open_edit(page, django_server, event, viewport)
        page.get_by_test_id("registrations-filter").fill("averyveryvery")
        visible_rows = page.get_by_test_id("registration-row").filter(visible=True)
        expect(visible_rows).to_have_count(1)
        expect(visible_rows).to_contain_text(LONG_EMAIL)

    visible_rows.get_by_test_id("registration-user-link").click()
    page.wait_for_load_state("domcontentloaded")
    assert re.search(r"/studio/users/\d+/?$", page.url), page.url
    expect(page.get_by_text(LONG_EMAIL).first).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_tablet_and_phone_widths_read_the_roster_as_cards(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    event = _seed_roster_event()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    for viewport in (TABLET_VIEWPORT, PHONE_VIEWPORT):
        _open_edit(page, django_server, event, viewport)
        panel = page.get_by_test_id("event-registrations-panel")
        panel.scroll_into_view_if_needed()
        expect(page.get_by_test_id("registrations-table")).to_be_hidden()
        cards = page.get_by_test_id("registration-card")
        expect(cards).to_have_count(4)
        expect(cards.first).to_be_visible()
        joined_card = cards.filter(has_text=JOINED_EMAIL)
        for label in ("Registered", "Tier", "Joined"):
            expect(joined_card.locator("dt", has_text=label)).to_be_visible()
        assert not page.evaluate(_PAGE_SCROLLS_SIDEWAYS), viewport
        # Reading order: the roster comes before the State side panel.
        assert panel.evaluate(
            "(el) => Boolean(el.compareDocumentPosition("
            "document.querySelector('[data-testid=\"studio-meta-actions-panel\"]'))"
            " & Node.DOCUMENT_POSITION_FOLLOWING)"
        )
        assert (
            panel.bounding_box()["y"]
            < page.get_by_test_id("studio-meta-actions-panel").bounding_box()["y"]
        )

    _open_edit(page, django_server, event, TABLET_VIEWPORT)
    page.get_by_test_id("registrations-filter").fill(JOINED_EMAIL)
    visible_cards = page.get_by_test_id("registration-card").filter(visible=True)
    expect(visible_cards).to_have_count(1)
    expect(visible_cards.get_by_test_id("registration-card-joined-badge")).to_have_text(
        "Joined",
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_feedback_panel_spans_the_same_full_width_as_the_roster(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    event = _seed_roster_event(feedback=True)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    _open_edit(page, django_server, event, LAPTOP_VIEWPORTS[0])
    feedback = page.get_by_test_id("event-feedback-panel")
    feedback.scroll_into_view_if_needed()
    expect(feedback.get_by_test_id("feedback-avg")).to_contain_text("4.0/5")
    expect(feedback.get_by_test_id("feedback-row")).to_have_count(2)
    expect(feedback).to_contain_text("The live debugging part was the most useful.")
    expect(feedback).to_contain_text("Slides were hard to read on a laptop.")

    geometry = page.evaluate(_PANEL_GEOMETRY, "event-feedback-panel")
    assert geometry["leftDelta"] <= 2, geometry
    assert geometry["rightDelta"] <= 2, geometry
    roster_box = page.get_by_test_id("event-registrations-panel").bounding_box()
    feedback_box = feedback.bounding_box()
    assert feedback_box["y"] >= roster_box["y"] + roster_box["height"]
    assert abs(feedback_box["width"] - roster_box["width"]) <= 2
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_operator_finds_leftover_grants_that_raise_nobody(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _seed_override_fixture()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")
    page.locator('#studio-section-people a[href="/studio/tier_overrides/"]').click()
    page.wait_for_load_state("domcontentloaded")

    expect(page.get_by_test_id("active-overrides-count")).to_have_text(
        "5 active · 3 not raising access",
    )
    expect(page.get_by_test_id("active-overrides-filter-all")).to_have_text("All (5)")
    expect(page.get_by_test_id("active-overrides-filter-effective")).to_have_text(
        "Raising access (2)",
    )
    ineffective_chip = page.get_by_test_id("active-overrides-filter-ineffective")
    expect(ineffective_chip).to_have_text("Not raising access (3)")
    expect(page.get_by_role("columnheader", name="Override tier")).to_be_visible()

    ineffective_chip.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith("/studio/tier_overrides/?status=ineffective")
    expect(page.get_by_test_id("active-overrides-filter-ineffective")).to_have_attribute(
        "aria-current", "page",
    )
    rows = page.get_by_test_id("active-override-row")
    expect(rows).to_have_count(3)
    assert [
        row.get_attribute("data-user-email") for row in rows.all()
    ] == [A_EMAIL, C_EMAIL, D_EMAIL]
    for row in rows.all():
        expect(row.get_by_test_id("active-override-ineffective")).to_have_text(
            "Not raising access",
        )
    d_row = rows.filter(has_text=D_EMAIL)
    expect(d_row.get_by_test_id("active-override-effective-tier-pill")).to_have_text("Main")
    expect(d_row.get_by_test_id("active-override-base-tier-pill")).to_have_text("Premium")
    expect(d_row.get_by_test_id("active-override-base-tier-at-grant")).to_have_text(
        "Free when granted",
    )
    expect(
        rows.filter(has_text=A_EMAIL).get_by_test_id("active-override-base-tier-at-grant"),
    ).to_have_count(0)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_operator_revokes_a_leftover_grant_and_stays_on_the_filter(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    grants = _seed_override_fixture()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(
        f"{django_server}/studio/tier_overrides/?status=ineffective",
        wait_until="domcontentloaded",
    )
    page.get_by_test_id("active-override-row").filter(has_text=A_EMAIL).get_by_test_id(
        "active-override-revoke",
    ).click()
    page.wait_for_load_state("domcontentloaded")

    assert page.url.endswith("/studio/tier_overrides/?status=ineffective"), page.url
    expect(page.get_by_text(f"Override revoked for {A_EMAIL}.")).to_be_visible()
    rows = page.get_by_test_id("active-override-row")
    expect(rows).to_have_count(2)
    expect(rows.filter(has_text=A_EMAIL)).to_have_count(0)
    expect(page.get_by_test_id("active-overrides-filter-ineffective")).to_have_text(
        "Not raising access (2)",
    )
    assert not _is_active(grants["a_basic"].pk)

    page.goto(
        f"{django_server}/studio/users/{_user_pk(A_EMAIL)}/",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("user-detail-tier-override-block")).not_to_contain_text(
        "Active override",
    )
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Main")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_effective_view_lists_only_comps_that_raise_access(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _seed_override_fixture()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/tier_overrides/?status=effective",
        wait_until="domcontentloaded",
    )
    rows = page.get_by_test_id("active-override-row")
    expect(rows).to_have_count(2)
    assert [
        row.get_by_test_id("active-override-effective-tier-pill").inner_text()
        for row in rows.all()
    ] == ["Premium", "Basic"]
    for row in rows.all():
        assert row.get_attribute("data-user-email") == B_EMAIL
        expect(row.get_by_test_id("active-override-ineffective")).to_have_count(0)

    rows.first.get_by_test_id("active-override-user-link").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Premium")
    expect(page.get_by_test_id("user-detail-tier-badge")).to_have_attribute(
        "data-tier-source", "override",
    )
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_clean_state_once_every_leftover_is_revoked(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _create_user(B_EMAIL, tier_slug="free", first_name="Ben")
    _grant(B_EMAIL, "premium", 10, original_tier_slug="free")
    _grant(B_EMAIL, "basic", 300, original_tier_slug="free")

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/tier_overrides/?status=ineffective",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_role("columnheader", name="Override tier")).to_be_visible()
    empty = page.get_by_test_id("studio-empty-state-filter")
    expect(empty).to_contain_text("No overrides match your filters.")

    empty.get_by_role("link", name="Clear filters").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith("/studio/tier_overrides/"), page.url
    expect(page.get_by_test_id("active-override-row")).to_have_count(2)
    expect(page.get_by_test_id("active-overrides-count")).to_have_text("2 active")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_leftover_grant_counts_again_after_the_paid_tier_lapses(
    django_server, browser,
):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _create_user(A_EMAIL, tier_slug="main", first_name="Ada")
    grant = _grant(A_EMAIL, "basic", 30, original_tier_slug="main")

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/tier_overrides/?status=ineffective",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("active-override-row")).to_have_count(1)
    expect(page.get_by_test_id("active-override-row")).to_have_attribute(
        "data-user-email", A_EMAIL,
    )

    _set_stored_tier(A_EMAIL, "free")
    page.goto(
        f"{django_server}/studio/tier_overrides/?status=effective",
        wait_until="domcontentloaded",
    )
    row = page.get_by_test_id("active-override-row")
    expect(row).to_have_count(1)
    expect(row).to_have_attribute("data-raises-access", "true")
    expect(row.get_by_test_id("active-override-base-tier-pill")).to_have_text("Free")
    expect(row.get_by_test_id("active-override-base-tier-at-grant")).to_have_text(
        "Main when granted",
    )
    assert _is_active(grant.pk)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_member_cannot_reach_the_tier_override_cleanup_list(django_server, browser):
    _reset()
    _create_staff_user(STAFF_EMAIL)
    _seed_override_fixture()
    _create_user(MEMBER_EMAIL, tier_slug="main")

    context = _auth_context(browser, MEMBER_EMAIL)
    page = context.new_page()
    response = page.goto(
        f"{django_server}/studio/tier_overrides/?status=ineffective",
        wait_until="domcontentloaded",
    )
    assert response.status == 403
    expect(page.get_by_test_id("active-overrides-section")).to_have_count(0)
    body = page.content()
    for email in (A_EMAIL, C_EMAIL, D_EMAIL):
        assert email not in body
    context.close()
