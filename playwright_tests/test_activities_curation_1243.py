"""Browser acceptance coverage for the canonical Membership journey (#1402)."""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    ensure_site_config_tiers,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


EXPLAINED_BENEFITS = [
    "Exclusive written content",
    "Workshop content",
    "Community sprints",
    "Live events",
    "Private Slack community",
    "Personalized onboarding plan",
    "Topic voting",
    "Courses",
]


def _seed_membership(previews=True):
    from django.db import connection

    from content.models import Workshop
    from events.models import Event
    from plans.models import Sprint

    ensure_tiers()
    ensure_site_config_tiers()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Sprint.objects.all().delete()

    if previews:
        now = timezone.now()
        today = timezone.localdate()
        Event.objects.create(
            title="Membership live building session",
            slug="membership-1243-live-building",
            start_datetime=now + datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=2, hours=1),
            status="upcoming",
            published=True,
        )
        Workshop.objects.create(
            title="Membership agent workshop",
            slug="membership-1243-agent-workshop",
            date=today - datetime.timedelta(days=2),
            status="published",
        )
        Sprint.objects.create(
            name="Membership shipping sprint",
            slug="membership-1243-shipping-sprint",
            start_date=today - datetime.timedelta(days=7),
            duration_weeks=4,
            status="active",
            min_tier_level=20,
        )
    connection.close()


def _top(locator):
    box = locator.bounding_box()
    assert box is not None
    return box["y"]


def test_legacy_activities_redirects_to_the_membership_benefits_anchor(
    django_server, page
):
    page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

    assert page.url.endswith("/membership#activities")
    expect(page.get_by_test_id("membership-benefits-section")).to_be_visible()


def test_legacy_pricing_preserves_query_on_membership_redirect(django_server, page):
    page.goto(
        f"{django_server}/pricing?utm_source=legacy",
        wait_until="domcontentloaded",
    )

    assert page.url.endswith('/membership?utm_source=legacy')


def test_plans_lead_into_shared_benefit_records_and_canonical_previews(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_membership()

    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    plans = page.get_by_test_id("pricing-tier-carousel")
    benefits = page.get_by_test_id("membership-benefits-section")
    sprints = page.get_by_test_id("membership-sprints-section")
    events = page.get_by_test_id("membership-live-events-section")
    workshops = page.get_by_test_id("membership-workshops-section")
    assert _top(plans) < _top(benefits) < _top(sprints) < _top(events) < _top(workshops)

    titles = page.get_by_test_id("membership-benefit-title").all_inner_texts()
    assert titles == EXPLAINED_BENEFITS
    assert page.get_by_test_id("membership-benefit-row").count() == 8
    assert page.locator(
        '[data-testid="membership-benefit-tier-badge"][data-required-level="10"]'
    ).count() == 2
    assert page.locator(
        '[data-testid="membership-benefit-tier-badge"][data-required-level="20"]'
    ).count() == 5
    assert page.locator(
        '[data-testid="membership-benefit-tier-badge"][data-required-level="30"]'
    ).count() == 1

    assert page.get_by_test_id("membership-sprint-card").count() == 1
    assert page.get_by_test_id("upcoming-event-card").count() == 1
    expect(page.get_by_test_id("events-timeline")).to_be_visible()
    expect(page.get_by_test_id("events-timeline-day")).to_be_visible()
    expect(page.get_by_test_id("timeline-day-date")).to_be_visible()
    expect(
        page.get_by_test_id("events-timeline-day").locator("span.bg-accent")
    ).to_be_visible()
    assert page.get_by_test_id("workshop-card").count() == 1
    expect(page.get_by_test_id("membership-sprint-card")).to_contain_text(
        "Membership shipping sprint"
    )
    expect(page.get_by_test_id("upcoming-event-card")).to_contain_text(
        "Membership live building session"
    )
    expect(page.get_by_test_id("workshop-card")).to_contain_text(
        "Membership agent workshop"
    )


def test_plan_bullets_inherit_without_repeating_plan_descriptions(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_membership(previews=False)

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    basic = page.locator('[data-tier-card="basic"]')
    main = page.locator('[data-tier-card="main"]')
    premium = page.locator('[data-tier-card="premium"]')
    basic_bullets = basic.locator("li span").all_inner_texts()
    main_bullets = main.locator("li span").all_inner_texts()
    premium_bullets = premium.locator("li span").all_inner_texts()

    assert basic_bullets == ["Exclusive written content", "Workshop content"]
    assert main_bullets[0] == "Everything in Basic"
    assert main_bullets[-1] == "Topic voting"
    assert premium_bullets == [
        "Everything in Main",
        "Courses",
        "Resume and LinkedIn teardown",
        "GitHub feedback",
    ]
    assert "Everything in Basic" not in main.locator("p").first.inner_text()
    assert "Everything in Main" not in premium.locator("p").first.inner_text()

    body = benefits_text = page.get_by_test_id("membership-benefits-list").inner_text()
    assert "Resume and LinkedIn teardown" not in benefits_text
    assert "GitHub feedback" not in body


def test_benefit_rows_keep_one_action_or_are_intentionally_static(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_membership(previews=False)

    page.goto(f"{django_server}/membership#activities", wait_until="domcontentloaded")
    rows = page.get_by_test_id("membership-benefit-row")

    assert rows.count() == 8
    assert rows.locator("a").count() == 6
    for title in ("Private Slack community", "Personalized onboarding plan"):
        row = rows.filter(has_text=title)
        assert row.locator("a").count() == 0
    expect(rows.filter(has_text="Personalized onboarding plan")).to_contain_text(
        "career plans and ambitions"
    )


def test_mobile_membership_is_centered_and_has_no_page_overflow(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_membership()

    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="Choose your level of engagement")).to_be_visible()
    expect(
        page.get_by_role("heading", name="Build with structure, support, and a group")
    ).to_be_visible()
    assert page.get_by_test_id("pricing-tier-card").count() == 4
    expect(page.get_by_test_id("timeline-day-date")).to_be_visible()
    expect(
        page.get_by_test_id("events-timeline-day").locator("span.bg-accent")
    ).to_be_visible()
    day_box = page.get_by_test_id("events-timeline-day").bounding_box()
    card_box = page.get_by_test_id("upcoming-event-card").bounding_box()
    assert day_box is not None
    assert card_box is not None
    assert 0 <= day_box["x"] < card_box["x"]
    assert card_box["x"] + card_box["width"] <= 390
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )
    context.close()


def test_membership_previews_grouped_series_after_canonical_row_construction(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_membership(previews=False)
        from django.db import connection

        from events.models import Event, EventSeries

        now = timezone.now()
        series = EventSeries.objects.create(
            name="Membership recurring program",
            slug="membership-recurring-program",
        )
        for position, days in enumerate((1, 8), start=1):
            Event.objects.create(
                title=f"Membership recurring session {position}",
                slug=f"membership-recurring-session-{position}",
                start_datetime=now + datetime.timedelta(days=days),
                end_datetime=now + datetime.timedelta(days=days, hours=1),
                timezone="Europe/Berlin",
                status="upcoming",
                event_series=series,
                series_position=position,
            )
        Event.objects.create(
            title="Membership later standalone",
            slug="membership-later-standalone",
            start_datetime=now + datetime.timedelta(days=10),
            status="upcoming",
        )
        series_url = series.get_absolute_url()
        connection.close()

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    card = page.get_by_test_id("event-series-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Membership recurring program")
    expect(card).to_contain_text("2 upcoming sessions")
    expect(page.get_by_test_id("events-timeline-day")).to_be_visible()
    assert page.get_by_test_id("upcoming-event-card").count() == 0
    expect(page.get_by_text("Membership later standalone")).to_have_count(0)
    expect(card.get_by_test_id("series-card-link")).to_have_attribute(
        "href",
        series_url,
    )


def test_registered_member_sees_events_timezone_and_registration_context(
    django_server, browser, django_db_blocker
):
    email = "membership-timeline-1402@test.com"
    with django_db_blocker.unblock():
        _seed_membership(previews=False)
        from django.contrib.auth import get_user_model
        from django.db import connection

        from events.models import Event, EventRegistration

        User = get_user_model()
        member = User.objects.create_user(
            email=email,
            password="pw",
            preferred_timezone="Asia/Kolkata",
        )
        other = User.objects.create_user(
            email="membership-other-attendee-1402@test.com",
            password="pw",
        )
        event = Event.objects.create(
            title="Membership registered timeline event",
            slug="membership-registered-timeline-event",
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            timezone="UTC",
            status="upcoming",
        )
        EventRegistration.objects.create(user=member, event=event)
        EventRegistration.objects.create(user=other, event=event)
        connection.close()

    context = _auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    membership_date = page.get_by_test_id("timeline-day-date").inner_text()
    membership_time = page.get_by_test_id("event-card-time").inner_text()
    expect(page.get_by_test_id("membership-live-events-list")).to_contain_text(
        "Registered"
    )
    expect(page.get_by_test_id("event-attendee-count")).to_have_text(
        "2 registered"
    )

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")
    expect(page.get_by_test_id("events-timezone-note")).to_contain_text(
        "Asia/Kolkata"
    )
    assert page.get_by_test_id("timeline-day-date").inner_text() == membership_date
    assert page.get_by_test_id("event-card-time").inner_text() == membership_time
    expect(page.get_by_test_id("event-attendee-count")).to_have_text(
        "2 registered"
    )
    context.close()


def test_quiet_week_keeps_all_preview_sections_useful(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_membership(previews=False)

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    expect(page.get_by_test_id("membership-sprints-empty")).to_contain_text(
        "Next sprint coming soon"
    )
    expect(page.get_by_test_id("membership-live-events-empty")).to_contain_text(
        "No live events scheduled yet"
    )
    expect(page.get_by_test_id("membership-workshops-empty")).to_contain_text(
        "No workshops published yet"
    )
