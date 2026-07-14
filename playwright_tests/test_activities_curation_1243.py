"""User journeys for the curated, self-contained activities page (#1243)."""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


CURATED_SLUGS = [
    "community-sprints",
    "live-events",
    "workshops",
    "slack-community",
    "personal-plans",
    "exclusive-content",
    "courses",
]


def _reset_fixtures():
    from django.db import connection

    from content.models import Workshop
    from events.models import Event
    from plans.models import Sprint

    Workshop.objects.filter(slug__startswith="activities-1243-").delete()
    Event.objects.filter(slug__startswith="activities-1243-").delete()
    Sprint.objects.filter(slug__startswith="activities-1243-").delete()
    connection.close()


def _seed_preview_fixtures():
    from django.db import connection

    from content.models import Workshop
    from events.models import Event
    from plans.models import Sprint

    _reset_fixtures()
    now = timezone.now()
    today = timezone.localdate()
    event = Event.objects.create(
        title="Activities live building session",
        slug="activities-1243-live-building",
        start_datetime=now + datetime.timedelta(days=5),
        end_datetime=now + datetime.timedelta(days=5, hours=1),
        status="upcoming",
        published=True,
    )
    workshop = Workshop.objects.create(
        title="Activities agent workshop",
        slug="activities-1243-agent-workshop",
        date=today - datetime.timedelta(days=2),
        status="published",
    )
    Sprint.objects.create(
        name="Activities shipping sprint",
        slug="activities-1243-shipping-sprint",
        start_date=today - datetime.timedelta(days=7),
        duration_weeks=4,
        status="active",
        min_tier_level=20,
    )
    connection.close()
    return event, workshop


def _wait_for_target_in_view(page, selector):
    page.wait_for_function(
        """selector => {
          const element = document.querySelector(selector);
          if (!element) return false;
          const rect = element.getBoundingClientRect();
          return rect.bottom > 0 && rect.top < window.innerHeight;
        }""",
        arg=selector,
    )


def test_visitor_scans_exact_curated_list_and_tier_answers(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _seed_preview_fixtures()

    page.goto(f"{django_server}/activities", wait_until="domcontentloaded")
    page.get_by_test_id("activities-grid").wait_for(state="visible")

    cards = page.get_by_test_id("activity-card")
    assert cards.count() == 7
    assert cards.evaluate_all("cards => cards.map(card => card.dataset.activity)") == CURATED_SLUGS
    expect(page.locator("main")).not_to_contain_text("Community Hackathons")
    expect(page.locator("main")).not_to_contain_text("Personal Brand Development")
    assert page.get_by_test_id("activities-tier-filter").count() == 0
    assert page.get_by_test_id("activities-tier-empty").count() == 0
    assert page.locator('[data-testid="activity-tier-badge"][data-tier="basic"][data-included="true"]').count() == 1
    assert page.locator('[data-testid="activity-tier-badge"][data-tier="main"][data-included="true"]').count() == 6
    assert page.locator('[data-testid="activity-tier-badge"][data-tier="premium"][data-included="true"]').count() == 7
    expect(page.get_by_test_id("activities-quick-comparison")).to_contain_text("1 activity")
    expect(page.get_by_test_id("activities-quick-comparison")).to_contain_text("6 activities")
    expect(page.get_by_test_id("activities-quick-comparison")).to_contain_text("All 7 activities")


@pytest.mark.parametrize(
    ("label", "fragment", "target_testid"),
    [
        ("Community sprints", "community-sprints", "activities-sprints-section"),
        ("Live events", "live-events", "activities-live-events-section"),
        ("Workshops", "workshops", "activities-workshops-section"),
    ],
)
def test_intro_quick_links_change_only_fragment_and_reveal_target(
    django_server, page, django_db_blocker, label, fragment, target_testid
):
    with django_db_blocker.unblock():
        _seed_preview_fixtures()

    page.goto(f"{django_server}/activities", wait_until="domcontentloaded")
    nav = page.get_by_test_id("activities-anchor-nav")
    nav.wait_for(state="visible")
    links = nav.get_by_role("link")
    assert links.count() == 3
    assert links.evaluate_all("links => links.map(link => link.getAttribute('href'))") == [
        "#community-sprints",
        "#live-events",
        "#workshops",
    ]

    nav.get_by_role("link", name=label, exact=True).click()
    page.wait_for_url(f"**/activities#{fragment}")
    assert page.evaluate("() => window.location.pathname") == "/activities"
    assert page.evaluate("() => window.location.hash") == f"#{fragment}"
    _wait_for_target_in_view(page, f'[data-testid="{target_testid}"]')


def test_visitor_previews_live_event_and_workshop_then_follows_real_destinations(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        event, workshop = _seed_preview_fixtures()
        event_url = event.get_absolute_url()
        workshop_url = workshop.get_absolute_url()

    page.goto(
        f"{django_server}/activities#live-events",
        wait_until="domcontentloaded",
    )
    event_card = page.get_by_test_id("activities-live-event-card")
    expect(event_card).to_contain_text("Activities live building session")
    expect(event_card.get_by_test_id("activities-live-event-date")).to_be_visible()
    event_card.get_by_test_id("activities-live-event-link").click()
    page.wait_for_url(f"**{event_url}")
    expect(page.get_by_role("heading", name="Activities live building session")).to_be_visible()

    page.goto(
        f"{django_server}/activities#workshops",
        wait_until="domcontentloaded",
    )
    workshop_card = page.get_by_test_id("activities-workshop-card")
    expect(workshop_card).to_contain_text("Activities agent workshop")
    assert workshop_card.get_by_test_id("activities-workshop-link").get_attribute("href") == workshop_url
    page.get_by_test_id("activities-view-all-workshops").click()
    page.wait_for_url("**/workshops")
    expect(page.get_by_role("heading", name="Hands-on AI workshops")).to_be_visible()


def test_quiet_week_keeps_preview_sections_and_pricing_path_useful(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _reset_fixtures()

    page.goto(
        f"{django_server}/activities#live-events",
        wait_until="domcontentloaded",
    )

    live_empty = page.get_by_test_id("activities-live-events-empty")
    expect(live_empty).to_contain_text("No live events scheduled yet")
    assert live_empty.get_by_role("link", name="View events").get_attribute("href") == "/events"
    workshops_empty = page.get_by_test_id("activities-workshops-empty")
    expect(workshops_empty).to_contain_text("No workshops published yet")
    assert workshops_empty.get_by_role("link", name="View workshops").get_attribute("href") == "/workshops"

    pricing = page.get_by_test_id("activities-pricing-cta")
    pricing.scroll_into_view_if_needed()
    pricing.click()
    page.wait_for_url("**/pricing")
    expect(page.get_by_role("heading", name="Choose your level of engagement")).to_be_visible()
