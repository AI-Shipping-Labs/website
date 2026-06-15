"""Playwright E2E for the Request a call feature (#870, Phase 1).

Covers the member-facing /request-a-call page and the Studio
call-host management flow:

- Onboarded member books a call with an available host (link + new tab).
- Member who hasn't onboarded is nudged to onboard first (no links).
- A full host shows "Not currently available" while another is bookable.
- Both hosts full -> helpful "check back" state, not a dead end.
- Anonymous visitor is redirected to login.
- Staff edits a host's booking link / capacity without a deploy and the
  member-facing page reflects it.
- Studio call-hosts page is staff-only.

Usage:
    uv run pytest playwright_tests/test_request_a_call_870.py -v
"""

import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: DB seeding + session-cookie injection. Cannot run against
# the deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

VALERIA_URL = "https://calendar.app.google/Rh5oWPU9ZAuuDLPt9"
ALEXEY_URL = "https://calendly.com/alexey-test/intro"


def _complete_onboarding(email):
    from django.db import connection

    from accounts.models import User
    from questionnaires.models import Questionnaire, Response
    from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG

    user = User.objects.get(email=email)
    # transaction=True flushes migration-seeded rows between tests, so the
    # onboarding questionnaire may not exist — recreate it if needed.
    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug=GENERIC_ONBOARDING_SLUG,
        defaults={"title": "Onboarding", "purpose": "onboarding"},
    )
    Response.objects.get_or_create(
        questionnaire=questionnaire, respondent=user,
        defaults={"status": "submitted"},
    )
    connection.close()


# Display names for the seed hosts so we can recreate them when
# transaction=True has flushed the migration-seeded rows.
_HOST_NAMES = {'alexey': 'Alexey Grigorev', 'valeria': 'Valeriia Kuka'}


def _set_host(slug, **fields):
    from django.db import connection

    from community.models import CallHost

    defaults = {'name': _HOST_NAMES.get(slug, slug.title()), **fields}
    CallHost.objects.update_or_create(slug=slug, defaults=defaults)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestOnboardedMemberBooksAvailableHost:
    @pytest.mark.core
    def test_available_host_shows_booking_link_in_new_tab(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_user("alice-870@test.com", tier_slug="free")
            _complete_onboarding("alice-870@test.com")
            _set_host(
                "valeria", is_active=True, capacity=5, current_load=0,
                booking_url=VALERIA_URL,
            )

        context = auth_context(browser, "alice-870@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            card = page.locator('[data-host-slug="valeria"]')
            card.wait_for(state="visible")
            book = card.locator('[data-testid="call-host-book"]')
            assert book.count() == 1
            assert book.get_attribute("href") == VALERIA_URL
            assert book.get_attribute("target") == "_blank"
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNotOnboardedMemberNudged:
    @pytest.mark.core
    def test_gate_shown_no_booking_links(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            # Issue #982: the "Finish onboarding" CTA is only handed to a
            # member who can enter the paid-only onboarding flow, so this
            # not-onboarded-nudge scenario uses a paid (Basic) member.
            create_user("bob-870@test.com", tier_slug="basic")
            _set_host(
                "valeria", is_active=True, capacity=5, booking_url=VALERIA_URL,
            )

        context = auth_context(browser, "bob-870@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            gate = page.locator('[data-testid="request-call-onboarding-gate"]')
            gate.wait_for(state="visible")
            cta = page.locator('[data-testid="request-call-onboarding-cta"]')
            assert cta.get_attribute("href") == "/onboarding/"
            assert page.locator('[data-testid="call-host-book"]').count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestFullHostShownUnavailable:
    def test_full_host_unavailable_other_bookable(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_user("carol-870@test.com", tier_slug="free")
            _complete_onboarding("carol-870@test.com")
            _set_host(
                "alexey", is_active=True, capacity=1, current_load=1,
                booking_url=ALEXEY_URL,
            )
            _set_host(
                "valeria", is_active=True, capacity=5, current_load=0,
                booking_url=VALERIA_URL,
            )

        context = auth_context(browser, "carol-870@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            alexey = page.locator('[data-host-slug="alexey"]')
            alexey.wait_for(state="visible")
            assert alexey.locator(
                '[data-testid="call-host-unavailable"]'
            ).count() == 1
            assert alexey.locator('[data-testid="call-host-book"]').count() == 0

            valeria = page.locator('[data-host-slug="valeria"]')
            assert valeria.locator('[data-testid="call-host-book"]').count() == 1
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestBothFullHelpfulState:
    def test_both_full_shows_check_back_not_dead_end(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_user("dan-870@test.com", tier_slug="free")
            _complete_onboarding("dan-870@test.com")
            _set_host("alexey", is_active=True, capacity=1, current_load=1)
            _set_host("valeria", is_active=True, capacity=1, current_load=1)

        context = auth_context(browser, "dan-870@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            page.locator(
                '[data-testid="request-call-check-back"]'
            ).wait_for(state="visible")
            assert page.locator('[data-host-slug="alexey"]').count() == 1
            assert page.locator('[data-host-slug="valeria"]').count() == 1
            assert page.locator('[data-testid="call-host-book"]').count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestAnonymousRedirectedToLogin:
    @pytest.mark.core
    def test_anonymous_redirected(self, django_server, browser):
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            assert "/accounts/login/" in page.url
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestStaffUpdatesBookingLinkWithoutDeploy:
    def test_staff_edit_reflected_on_member_page(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user("admin-870@test.com")
            create_user("member-870@test.com", tier_slug="free")
            _complete_onboarding("member-870@test.com")
            _set_host(
                "valeria", is_active=True, capacity=1, current_load=0,
                booking_url=VALERIA_URL,
            )
            from community.models import CallHost
            host_id = CallHost.objects.get(slug="valeria").pk

        new_url = "https://calendar.app.google/UPDATED1234567"
        staff_ctx = auth_context(browser, "admin-870@test.com")
        try:
            page = staff_ctx.new_page()
            page.goto(
                f"{django_server}/studio/call-hosts/{host_id}/edit",
                wait_until="domcontentloaded",
            )
            page.fill('input[name="booking_url"]', new_url)
            page.fill('input[name="capacity"]', "9")
            page.click('button[type="submit"]')
            page.wait_for_url(f"{django_server}/studio/call-hosts/")
            # List shows the host as available after raising capacity.
            row = page.locator('[data-host-slug="valeria"]')
            assert "Available" in row.locator(
                '[data-testid="host-availability"]'
            ).inner_text()
        finally:
            staff_ctx.close()

        member_ctx = auth_context(browser, "member-870@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/request-a-call",
                wait_until="domcontentloaded",
            )
            book = page.locator(
                '[data-host-slug="valeria"] [data-testid="call-host-book"]'
            )
            book.wait_for(state="visible")
            assert book.get_attribute("href") == new_url
        finally:
            member_ctx.close()


@pytest.mark.django_db(transaction=True)
class TestStudioCallHostsStaffOnly:
    @pytest.mark.core
    def test_non_staff_member_forbidden(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_user("nonstaff-870@test.com", tier_slug="free")

        context = auth_context(browser, "nonstaff-870@test.com")
        try:
            page = context.new_page()
            response = page.goto(
                f"{django_server}/studio/call-hosts/",
                wait_until="domcontentloaded",
            )
            assert response.status == 403
        finally:
            context.close()
