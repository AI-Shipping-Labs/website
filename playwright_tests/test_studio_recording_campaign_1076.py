"""Playwright E2E for the recording-available campaign flow (issue #1076).

Covers the operator-facing entry points and audience resolution:

- Opening the pre-fill flow (the URL the host email links to) pre-selects the
  event audience, fills the body with the workshop write-up, and shows the
  registrant count (not the whole subscriber base).
- The Studio event page exposes a distinct "Email registrants: recording
  available" button that lands on the pre-fill form.
- Tier filter narrows the registrant audience (ANDs, not unions).
- Unsubscribed registrants are excluded.
- An event with no registrants resolves to a 0 audience.
- Non-staff cannot reach the pre-fill flow.
- Opening/saving never sends.
"""

import os
from datetime import datetime
from datetime import timezone as dt_timezone

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
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

UTC = dt_timezone.utc


def _reset():
    from email_app.models import EmailCampaign, EmailLog
    from events.models import Event, EventRegistration

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _make_event(title="Shipping Agents Workshop", slug="shipping-agents-wk",
                with_workshop=True):
    from datetime import date

    from content.models import Workshop
    from events.models import Event

    historical_event_date = date(2026, 6, 8)
    historical_start = datetime(2026, 6, 8, 16, 0, tzinfo=UTC)
    historical_end = datetime(2026, 6, 8, 17, 0, tzinfo=UTC)
    event = Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=historical_start,
        end_datetime=historical_end,
        status="completed",
        recording_url="https://youtube.com/watch?v=agents",
    )
    if with_workshop:
        Workshop.objects.create(
            slug=f"{slug}-ws",
            title=f"{title} write-up",
            date=historical_event_date,
            description="The complete WORKSHOP WRITEUP body for members.",
            event=event,
        )
    connection.close()
    return event


def _register(event, email, tier_slug="free", unsubscribed=False):
    from events.models import EventRegistration

    user = _create_user(
        email, tier_slug=tier_slug, email_verified=True,
        unsubscribed=unsubscribed,
    )
    EventRegistration.objects.get_or_create(event=event, user=user)
    connection.close()
    return user


def _prefill_path(event_id):
    return f"/studio/campaigns/new?event={event_id}&template=recording_available"


@pytest.mark.django_db(transaction=True)
class TestRecordingPrefillFromHostEmailLink:
    def test_prefill_preselects_event_and_shows_registrant_count(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_staff_user("admin@test.com")
        event = _make_event()
        for i in range(3):
            _register(event, f"reg{i}@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}{_prefill_path(event.pk)}",
            wait_until="domcontentloaded",
        )

        # Event pre-selected as the audience.
        selected = page.locator(
            '[data-testid="campaign-target-event"]'
        ).input_value()
        assert selected == str(event.pk)
        # Body pre-filled with the workshop write-up.
        body = page.locator('textarea[name="body"]').input_value()
        assert "WORKSHOP WRITEUP" in body
        # Recipient count reflects the 3 registrants, not all subscribers.
        helper = page.locator(
            '[data-testid="recipient-count-helper"]'
        ).inner_text()
        assert "3 eligible" in helper

        # Saving the draft does not send.
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/campaigns/" in page.url

        from email_app.models import EmailCampaign, EmailLog
        campaign = EmailCampaign.objects.latest("created_at")
        assert campaign.target_event_id == event.pk
        assert campaign.status == "draft"
        assert not EmailLog.objects.filter(email_type="campaign").exists()
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestRecordingButtonOnEventPage:
    def test_event_page_button_lands_on_prefill(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_staff_user("admin@test.com")
        event = _make_event(title="Recap Event", slug="recap-event-1076")
        _register(event, "attendee@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        button = page.locator(
            '[data-testid="email-registrants-recording-button"]'
        )
        assert button.is_visible()
        # Distinct from the transactional follow-up button.
        assert page.locator(
            '[data-testid="send-followup-button"], '
            '[data-testid="send-followup-button-disabled"]'
        ).count() >= 1

        button.click()
        page.wait_for_load_state("domcontentloaded")
        assert _prefill_path(event.pk).split("?")[0] in page.url
        selected = page.locator(
            '[data-testid="campaign-target-event"]'
        ).input_value()
        assert selected == str(event.pk)
        connection.close()


@pytest.mark.django_db(transaction=True)
class TestRegistrantAudienceNarrowing:
    def test_tier_filter_narrows_registrants(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_staff_user("admin@test.com")
        event = _make_event(title="Mixed Tier", slug="mixed-tier-1076")
        _register(event, "free1@test.com", tier_slug="free")
        _register(event, "free2@test.com", tier_slug="free")
        _register(event, "main1@test.com", tier_slug="main")
        _register(event, "main2@test.com", tier_slug="main")
        _register(event, "main3@test.com", tier_slug="main")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}{_prefill_path(event.pk)}",
            wait_until="domcontentloaded",
        )

        # Save a draft, then narrow to Main+ on the edit form and re-save.
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")
        from email_app.models import EmailCampaign
        campaign = EmailCampaign.objects.latest("created_at")
        connection.close()

        # Default audience: all 5 registrants.
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="eligible-recipients"]'
        ).inner_text().strip() == "5"

        # Narrow to Main+ via the edit form.
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('select[name="target_min_level"]').select_option("20")
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator(
            '[data-testid="eligible-recipients"]'
        ).inner_text().strip() == "3"


@pytest.mark.django_db(transaction=True)
class TestUnsubscribedAndEmptyAudience:
    def test_unsubscribed_registrant_excluded(self, django_server, browser):
        _ensure_tiers()
        _reset()
        _create_staff_user("admin@test.com")
        event = _make_event(title="Unsub Event", slug="unsub-event-1076")
        _register(event, "keep1@test.com")
        _register(event, "keep2@test.com")
        _register(event, "gone@test.com", unsubscribed=True)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}{_prefill_path(event.pk)}",
            wait_until="domcontentloaded",
        )
        helper = page.locator(
            '[data-testid="recipient-count-helper"]'
        ).inner_text()
        assert "2 eligible" in helper

    def test_event_with_no_registrants_reads_zero(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset()
        _create_staff_user("admin@test.com")
        event = _make_event(title="Empty Event", slug="empty-event-1076")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="email-registrants-recording-button"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")
        helper = page.locator(
            '[data-testid="recipient-count-helper"]'
        ).inner_text()
        assert "0 eligible" in helper


@pytest.mark.django_db(transaction=True)
class TestNonStaffBlocked:
    def test_non_staff_cannot_reach_prefill(self, django_server, browser):
        _ensure_tiers()
        _reset()
        event = _make_event(title="Gated", slug="gated-event-1076")
        _create_user("main@test.com", tier_slug="main", is_staff=False)

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}{_prefill_path(event.pk)}",
            wait_until="domcontentloaded",
        )
        # Either redirected away from the studio route or shown a 403 —
        # never the campaign form for a non-staff user.
        assert page.locator(
            '[data-testid="campaign-target-event"]'
        ).count() == 0
