"""Tier overrides agree across the API, access, and Studio (issue #1933).

A member can hold several active overrides at once (a staff grant next to a
Maven grant). The API, access checks, and every Studio surface resolve the
strongest one; Studio only signals "comped" when that override actually
raises the member above their stored tier. The event roster keeps every
column inside its own cell at laptop widths.
"""

import csv
import datetime
import io
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

STAFF_EMAIL = "studio-override-1933@test.com"
STACKED_EMAIL = "stacked-1933@test.com"
LEFTOVER_EMAIL = "leftover-1933@test.com"
COMPED_MAIN_EMAIL = "comped-main-1933@test.com"
FREE_EMAIL = "free-attendee-1933@test.com"
JOINED_EMAIL = "joined-attendee-1933@test.com"
LONG_EMAIL = (
    "averyveryveryverylongfirstname.lastname.2026@example-company-mail.com"
)
EVENT_SLUG = "roster-columns-1933"
ROSTER_VIEWPORTS = (
    {"width": 1280, "height": 800},
    {"width": 1024, "height": 768},
    {"width": 800, "height": 900},
)
MOBILE_VIEWPORT = {"width": 390, "height": 844}


def _expiry_label(expires_at):
    """Global-search / roster ``until`` text for the fixture expiry."""
    expires_at = expires_at.astimezone(datetime.timezone.utc)
    label = f"{expires_at:%b} {expires_at.day}"
    if expires_at.year != timezone.now().astimezone(datetime.timezone.utc).year:
        label = f"{label}, {expires_at.year}"
    return label


def _operator_datetime(value):
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _grant(email, tier_slug, expires_at, *, source="", granted_by=None,
           created_at=None):
    from accounts.models import User
    from payments.models import Tier, TierOverride

    override = TierOverride.objects.create(
        user=User.objects.get(email=email),
        override_tier=Tier.objects.get(slug=tier_slug),
        expires_at=expires_at,
        source=source,
        granted_by=granted_by,
    )
    if created_at is not None:
        TierOverride.objects.filter(pk=override.pk).update(created_at=created_at)
    connection.close()
    return override


def _reset():
    from events.models import Event
    from payments.models import TierOverride

    TierOverride.objects.all().delete()
    Event.objects.filter(slug=EVENT_SLUG).delete()
    connection.close()


def _seed_staff():
    staff = _create_staff_user(STAFF_EMAIL)
    connection.close()
    return staff


def _seed_stacked(staff):
    """Free member: staff Premium (10 days old) plus a newer Maven Basic."""
    member = _create_user(STACKED_EMAIL, first_name="Stacked")
    now = timezone.now()
    premium_expires = now + datetime.timedelta(days=20)
    premium = _grant(
        STACKED_EMAIL, "premium", premium_expires, granted_by=staff,
        created_at=now - datetime.timedelta(days=10),
    )
    _grant(
        STACKED_EMAIL, "basic", now + datetime.timedelta(days=300),
        source="maven:ai-buildcamp-1933",
    )
    return member, premium, premium_expires


def _seed_leftover():
    """Stored Main (no Stripe) with a leftover Basic comp."""
    member = _create_user(LEFTOVER_EMAIL, tier_slug="main", first_name="Leftover")
    _grant(LEFTOVER_EMAIL, "basic", timezone.now() + datetime.timedelta(days=30))
    return member


def _staff_token(staff):
    from accounts.models import Token

    token = Token.objects.create(user=staff, name="override-1933")
    key = token.key
    connection.close()
    return key


def _api_user(context, base_url, token, email):
    response = context.request.get(
        f"{base_url}/api/users/{email}",
        headers={"Authorization": f"Token {token}"},
    )
    return response.json()


def _users_page(page, base_url, query):
    page.goto(
        f"{base_url}/studio/users/?q={query}", wait_until="domcontentloaded",
    )


def _comped_counts(page):
    return {
        testid: int(page.get_by_test_id(testid).inner_text())
        for testid in (
            "override-basic", "override-main", "override-premium",
            "total-comped",
        )
    }


def _accept_dialogs(page):
    page.on("dialog", lambda dialog: dialog.accept())


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_stacked_member_shows_premium_on_every_studio_surface(
    django_server, browser,
):
    from events.models import Event, EventRegistration

    _reset()
    staff = _seed_staff()
    member, _, premium_expires = _seed_stacked(staff)
    event = Event.objects.create(
        title="Override roster 1933", slug=EVENT_SLUG, status="upcoming",
        start_datetime=timezone.now() + datetime.timedelta(days=5),
    )
    EventRegistration.objects.create(event=event, user=member)
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    _users_page(page, django_server, "stacked-1933")
    expect(page.get_by_test_id("user-list-tier-pill")).to_have_text("Premium")
    expect(page.get_by_test_id("user-list-tier-override-pill")).to_be_visible()

    page.goto(
        f"{django_server}/studio/users/{member.pk}/",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Premium")
    expect(page.get_by_test_id("user-detail-tier-badge")).to_have_attribute(
        "data-tier-source", "override",
    )
    expect(page.get_by_test_id("user-detail-tier-base")).to_have_text(
        re.compile(
            r"Base: Free\s+·\s+expires "
            + re.escape(_operator_datetime(premium_expires)) + " UTC",
        ),
    )

    page.get_by_test_id("studio-global-search-input").fill("stacked-1933")
    option = page.get_by_test_id("studio-global-search-results").get_by_role(
        "option", name=re.compile(re.escape(STACKED_EMAIL)),
    )
    expect(option.locator("[data-studio-search-result-metadata]")).to_have_text(
        re.compile(
            r"^Premium \(override until "
            + re.escape(_expiry_label(premium_expires)) + r"\)",
        ),
    )

    page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )
    expect(
        page.get_by_test_id("registration-row").get_by_test_id("registration-tier"),
    ).to_have_text(f"Premium (override until {_expiry_label(premium_expires)})")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_api_reports_the_tier_the_member_can_actually_read(
    django_server, browser,
):
    from content.models import Article

    _reset()
    staff = _seed_staff()
    _seed_stacked(staff)
    token = _staff_token(staff)
    Article.objects.filter(slug="premium-only-1933").delete()
    Article.objects.create(
        title="Premium only 1933",
        slug="premium-only-1933",
        description="Premium deep dive.",
        content_markdown="# Premium only\n\nThe premium body is readable.",
        required_level=30,
        published=True,
        date=datetime.date(2026, 1, 1),
    )
    connection.close()

    staff_context = _auth_context(browser, STAFF_EMAIL)
    detail = _api_user(staff_context, django_server, token, STACKED_EMAIL)
    assert detail["tier"] == {"slug": "premium", "level": 30, "source": "override"}
    assert detail["tier_override"]["tier_slug"] == "premium"
    listing = staff_context.request.get(
        f"{django_server}/api/users?q=stacked-1933",
        headers={"Authorization": f"Token {token}"},
    ).json()
    assert [row["tier"]["slug"] for row in listing["users"]] == ["premium"]
    staff_context.close()

    member_context = _auth_context(browser, STACKED_EMAIL)
    page = member_context.new_page()
    page.goto(
        f"{django_server}/blog/premium-only-1933", wait_until="domcontentloaded",
    )
    expect(page.get_by_text("The premium body is readable.")).to_be_visible()
    expect(page.get_by_test_id("gated-pricing-link")).to_have_count(0)
    member_context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_revoking_premium_grant_falls_back_to_maven_basic(django_server, browser):
    _reset()
    staff = _seed_staff()
    member, _, _ = _seed_stacked(staff)
    token = _staff_token(staff)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    _accept_dialogs(page)
    page.goto(
        f"{django_server}/studio/users/{member.pk}/",
        wait_until="domcontentloaded",
    )
    page.get_by_test_id("user-detail-tier-override-revoke").click()
    page.wait_for_load_state("domcontentloaded")

    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Basic")
    expect(page.get_by_test_id("user-detail-tier-badge")).to_have_attribute(
        "data-tier-source", "override",
    )
    detail = _api_user(context, django_server, token, STACKED_EMAIL)
    assert detail["tier"]["slug"] == "basic"
    assert detail["tier_override"]["tier_slug"] == "basic"
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_leftover_comp_on_main_member_is_not_counted_or_badged(
    django_server, browser,
):
    _reset()
    _seed_staff()
    member = _create_user(LEFTOVER_EMAIL, tier_slug="main", first_name="Leftover")
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    _accept_dialogs(page)
    _users_page(page, django_server, "leftover-1933")
    before = _comped_counts(page)

    _grant(LEFTOVER_EMAIL, "basic", timezone.now() + datetime.timedelta(days=30))
    _users_page(page, django_server, "leftover-1933")
    expect(page.get_by_test_id("user-list-tier-pill")).to_have_text("Main")
    expect(page.get_by_test_id("user-list-tier-override-pill")).to_have_count(0)
    assert _comped_counts(page) == before

    page.goto(
        f"{django_server}/studio/users/{member.pk}/",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Main")
    expect(page.get_by_test_id("user-detail-tier-badge")).to_have_attribute(
        "data-tier-source", "default",
    )
    expect(page.get_by_test_id("user-detail-tier-base")).to_have_count(0)
    block = page.get_by_test_id("user-detail-tier-override-block")
    expect(block).to_contain_text("Active override to Basic")
    expect(page.get_by_test_id("user-detail-tier-override-ineffective")).to_have_text(
        "Not raising access: the stored tier (Main) is already at or above Basic.",
    )

    page.get_by_test_id("user-detail-tier-override-revoke").click()
    page.wait_for_load_state("domcontentloaded")
    expect(block).not_to_contain_text("Active override")
    expect(page.get_by_test_id("user-detail-tier-override-revoke")).to_have_count(0)
    expect(page.get_by_test_id("user-detail-tier-pill")).to_have_text("Main")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_users_csv_export_matches_the_screen(django_server, browser):
    _reset()
    staff = _seed_staff()
    _seed_stacked(staff)
    _seed_leftover()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")
    with page.expect_download() as download_info:
        page.get_by_role("link", name="Export CSV").click()
    with open(download_info.value.path(), encoding="utf-8") as handle:
        rows = {
            row["email"]: row["tier"]
            for row in csv.DictReader(io.StringIO(handle.read()))
        }
    assert rows[STACKED_EMAIL] == "Premium (override)"
    assert rows[LEFTOVER_EMAIL] == "Main"
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_comped_counts_include_only_overrides_that_raise_access(
    django_server, browser,
):
    _reset()
    staff = _seed_staff()
    _create_user(COMPED_MAIN_EMAIL)
    _grant(COMPED_MAIN_EMAIL, "main", timezone.now() + datetime.timedelta(days=30))
    _seed_stacked(staff)
    _seed_leftover()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")
    counts = _comped_counts(page)
    assert counts == {
        "override-basic": 0,
        "override-main": 1,
        "override-premium": 1,
        "total-comped": 2,
    }
    context.close()


def _seed_roster_event(staff):
    from accounts.models import User
    from events.models import Event, EventRegistration

    member, _, _ = _seed_stacked(staff)
    free = _create_user(FREE_EMAIL, first_name="Fran")
    long_member = _create_user(LONG_EMAIL)
    User.objects.filter(pk=long_member.pk).update(
        first_name="Bartholomew-Alexandrina",
        last_name="Featherstonehaugh-Montgomery",
    )
    joined = _create_user(JOINED_EMAIL, tier_slug="basic", first_name="Joan")
    event = Event.objects.create(
        title="Roster columns 1933", slug=EVENT_SLUG, status="upcoming",
        start_datetime=timezone.now() + datetime.timedelta(days=5),
    )
    registered_at = datetime.datetime(
        2026, 10, 9, 13, 9, tzinfo=datetime.timezone.utc,
    )
    for user in (free, long_member, joined, member):
        registration = EventRegistration.objects.create(event=event, user=user)
        EventRegistration.objects.filter(pk=registration.pk).update(
            registered_at=registered_at,
            joined_at=registered_at if user == joined else None,
        )
    connection.close()
    return event


# Per row: every cell keeps its content inside its own box, and the
# Registered text never reaches into the Tier cell.
_ROW_OVERFLOWS = """
(rows) => rows.map((row) => {
  const cells = Array.from(row.querySelectorAll('td'));
  const overflowing = cells
    .filter((td) => td.scrollWidth > td.clientWidth)
    .map((td) => td.dataset.testid || td.cellIndex);
  const range = document.createRange();
  range.selectNodeContents(cells[2]);
  const registeredRight = range.getBoundingClientRect().right;
  const tierLeft = cells[3].getBoundingClientRect().left;
  return {overflowing, registeredIntoTier: registeredRight > tierLeft};
})
"""


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_roster_columns_stay_inside_their_cells_at_laptop_widths(
    django_server, browser,
):
    _reset()
    staff = _seed_staff()
    event = _seed_roster_event(staff)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    for viewport in ROSTER_VIEWPORTS:
        page.set_viewport_size(viewport)
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        table = page.get_by_test_id("registrations-table")
        table.scroll_into_view_if_needed()
        expect(table).to_be_visible()
        headers = table.locator("thead th")
        assert [
            text.strip() for text in headers.all_text_contents()
        ] == ["Name", "Email", "Registered", "Tier", "Joined"]

        rows = page.get_by_test_id("registration-row")
        expect(rows).to_have_count(4)
        report = rows.evaluate_all(_ROW_OVERFLOWS)
        assert report == [
            {"overflowing": [], "registeredIntoTier": False},
        ] * 4, (viewport, report)

        free_row = rows.filter(has_text=FREE_EMAIL)
        expect(free_row.get_by_test_id("registration-tier")).to_have_text("Free")
        joined_row = rows.filter(has_text=JOINED_EMAIL)
        expect(joined_row.get_by_test_id("registration-joined-badge")).to_be_visible()
        expect(joined_row.get_by_test_id("registration-joined-at")).to_have_text(
            "2026-10-09 13:09",
        )

    # Narrowest laptop width wraps the timestamp onto two lines.
    registered = rows.first.get_by_test_id("registration-registered")
    assert registered.evaluate(
        "el => el.getBoundingClientRect().height > "
        "parseFloat(getComputedStyle(el).lineHeight) * 1.5"
    )

    page.get_by_test_id("registrations-filter").fill("averyveryvery")
    expect(rows.filter(visible=True)).to_have_count(1)
    expect(rows.filter(visible=True)).to_contain_text(LONG_EMAIL)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_phone_roster_shows_stacked_cards_with_override(django_server, browser):
    _reset()
    staff = _seed_staff()
    event = _seed_roster_event(staff)
    from payments.models import TierOverride

    premium_expires = TierOverride.objects.get(
        user__email=STACKED_EMAIL, override_tier__slug="premium",
    ).expires_at
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(
        f"{django_server}/studio/events/{event.pk}/edit",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("registrations-table")).to_be_hidden()
    cards = page.get_by_test_id("registration-card")
    expect(cards).to_have_count(4)
    stacked_card = cards.filter(has_text=STACKED_EMAIL)
    stacked_card.scroll_into_view_if_needed()
    expect(stacked_card.get_by_test_id("registration-card-tier")).to_have_text(
        f"Premium (override until {_expiry_label(premium_expires)})",
    )
    for label in ("Registered", "Tier", "Joined"):
        expect(stacked_card.locator("dt", has_text=label)).to_be_visible()
    context.close()
