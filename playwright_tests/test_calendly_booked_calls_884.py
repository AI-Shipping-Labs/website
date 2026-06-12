"""Playwright E2E for Calendly booked-call capture (#884, Phase 2).

The OAuth round-trip is [HUMAN]; these cover the webhook -> CRM ->
availability loop with mocked Calendly payloads:

- A mocked invitee.created webhook for a member surfaces a booked call on
  that member's CRM record.
- Booking consumes the host's capacity automatically, so /request-a-call
  reflects the reduced availability; a matching invitee.canceled restores
  it.

Usage:
    uv run pytest playwright_tests/test_calendly_booked_calls_884.py -v
"""

import json
import os

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_staff_user,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

ALEXEY_URL = "https://calendly.com/alexey-884/intro"
EVENT_URI = "https://api.calendly.com/scheduled_events/EVT884"


def _set_host(slug, **fields):
    from django.db import connection

    from community.models import CallHost

    defaults = {"name": "Alexey Grigorev", **fields}
    CallHost.objects.update_or_create(slug=slug, defaults=defaults)
    connection.close()


def _track_in_crm(email):
    from django.db import connection

    from accounts.models import User
    from crm.models import CRMRecord

    user = User.objects.get(email=email)
    record, _ = CRMRecord.objects.get_or_create(user=user)
    connection.close()
    return record.pk


def _complete_onboarding(email):
    from django.db import connection

    from accounts.models import User
    from questionnaires.models import Questionnaire, Response
    from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG

    user = User.objects.get(email=email)
    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug=GENERIC_ONBOARDING_SLUG,
        defaults={"title": "Onboarding", "purpose": "onboarding"},
    )
    Response.objects.get_or_create(
        questionnaire=questionnaire, respondent=user,
        defaults={"status": "submitted"},
    )
    connection.close()


def _webhook_payload(event, email, host_url):
    return {
        "event": event,
        "payload": {
            "email": email,
            "name": "Alice Member",
            "uri": f"{EVENT_URI}/invitees/INV1",
            "scheduling_url": host_url,
            "scheduled_event": {
                "uri": EVENT_URI,
                "start_time": "2099-06-01T15:00:00.000000Z",
            },
        },
    }


def _post_webhook(context, django_server, event, email, host_url):
    response = context.request.post(
        f"{django_server}/api/webhooks/calendly",
        data=json.dumps(_webhook_payload(event, email, host_url)),
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 200, response.text()


@pytest.mark.django_db(transaction=True)
class TestBookedCallShowsOnCrmRecord:
    @pytest.mark.core
    def test_invitee_created_surfaces_on_crm(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user("admin-884@test.com")
            create_user("alice-884@test.com", tier_slug="free")
            _set_host(
                "alexey", is_active=True, capacity=2, current_load=0,
                booking_url=ALEXEY_URL,
            )
            crm_id = _track_in_crm("alice-884@test.com")

        context = auth_context(browser, "admin-884@test.com")
        try:
            _post_webhook(
                context, django_server, "invitee.created",
                "alice-884@test.com", ALEXEY_URL,
            )
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/crm/{crm_id}/",
                wait_until="domcontentloaded",
            )
            section = page.locator('[data-testid="crm-booked-calls-section"]')
            section.wait_for(state="visible")
            item = page.locator('[data-testid="crm-booked-call-item"]')
            assert item.count() == 1
            assert "Alexey Grigorev" in item.inner_text()
            assert "2099-06-01" in item.inner_text()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestBookingConsumesCapacity:
    @pytest.mark.core
    def test_booking_then_cancel_round_trips_availability(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user("admin-884b@test.com")
            create_user("alice-884b@test.com", tier_slug="free")
            _complete_onboarding("alice-884b@test.com")
            # Capacity 1 so a single booking flips the host to full.
            _set_host(
                "alexey", is_active=True, capacity=1, current_load=0,
                booking_url=ALEXEY_URL,
            )

        staff_ctx = auth_context(browser, "admin-884b@test.com")
        member_ctx = auth_context(browser, "alice-884b@test.com")
        try:
            # Before booking: host is bookable on /request-a-call.
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            card = page.locator('[data-host-slug="alexey"]')
            card.wait_for(state="visible")
            assert card.locator('[data-testid="call-host-book"]').count() == 1

            # Book via the webhook -> capacity consumed.
            _post_webhook(
                staff_ctx, django_server, "invitee.created",
                "alice-884b@test.com", ALEXEY_URL,
            )
            page.reload(wait_until="domcontentloaded")
            card = page.locator('[data-host-slug="alexey"]')
            card.wait_for(state="visible")
            # Host is now full: no booking link is offered.
            assert card.locator('[data-testid="call-host-book"]').count() == 0

            # Cancel via the webhook -> capacity restored.
            _post_webhook(
                staff_ctx, django_server, "invitee.canceled",
                "alice-884b@test.com", ALEXEY_URL,
            )
            page.reload(wait_until="domcontentloaded")
            card = page.locator('[data-host-slug="alexey"]')
            card.wait_for(state="visible")
            assert card.locator('[data-testid="call-host-book"]').count() == 1
        finally:
            staff_ctx.close()
            member_ctx.close()
