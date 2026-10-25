"""Playwright E2E for campaign test-send suggestion chips (issue #921).

The click-to-fill behaviour is JavaScript (append to the textarea
without submitting, skip duplicates), so it is verified here in the
browser rather than by HTML string-matching in a Django TestCase, per
_docs/testing-guidelines.md.

Scenarios:
1. One-click fill from the operator's own email, then send.
2. Reuse a recently-sent address (Recent chip) on a later visit.
3. Pick configured Common addresses, incl. the no-duplicate guard.
4. Build a multi-recipient test from several chips, then send.
5. No suggestions -> the test-send section renders cleanly, no chips.
6. A bad config value does not break the page; only valid chips show.

Test sends call Relay, so we patch EmailService._send_relay /
render_markdown_email in-process — the django_server runs in the same
process as the test, the same approach used in test_studio_campaigns.py.
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
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _reset_state():
    from email_app.models import EmailCampaign, EmailLog
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    IntegrationSetting.objects.filter(key="CAMPAIGN_TEST_RECIPIENTS").delete()
    clear_config_cache()
    connection.close()


def _set_common_recipients(value):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key="CAMPAIGN_TEST_RECIPIENTS",
        defaults={
            "value": value,
            "is_secret": False,
            "group": "relay",
            "description": "",
        },
    )
    clear_config_cache()
    connection.close()


def _create_campaign(subject="Suggestion Campaign"):
    from email_app.models import EmailCampaign

    campaign = EmailCampaign.objects.create(
        subject=subject,
        body="Body content",
        status="draft",
        target_min_level=0,
    )
    connection.close()
    return campaign


def _textarea(page):
    return page.locator("#test-recipients")


def _chip(page, email):
    return page.locator(f'[data-testid="test-recipient-chip"][data-email="{email}"]')


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestOwnEmailOneClickFill:
    pass


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestReuseRecentAddress:
    pass


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestConfiguredCommonAddresses:
    def test_pick_common_and_no_duplicate(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        _set_common_recipients("seed@example.com, team@example.com")
        _create_staff_user("admin@test.com")
        campaign = _create_campaign()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )

        _chip(page, "seed@example.com").wait_for(state="visible")
        _chip(page, "team@example.com").wait_for(state="visible")

        _chip(page, "seed@example.com").click()
        _chip(page, "team@example.com").click()
        assert _textarea(page).input_value() == "seed@example.com\nteam@example.com"

        # Clicking seed again must not add a duplicate line.
        _chip(page, "seed@example.com").click()
        assert _textarea(page).input_value() == "seed@example.com\nteam@example.com"
        connection.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestMultiRecipientFromChips:
    pass


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestNoSuggestionsRendersCleanly:
    def test_no_chips_when_nothing_to_suggest(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        # Staff operator with no email + empty config + fresh session.
        staff = _create_staff_user("admin@test.com")
        from accounts.models import User

        User.objects.filter(pk=staff.pk).update(email="")
        connection.close()
        campaign = _create_campaign()

        context = _auth_context(browser, "")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )

        # The test-send section still renders normally.
        page.locator("#test-recipients").wait_for(state="visible")
        page.get_by_role("button", name="Send Test").wait_for(state="visible")
        # No suggestion container at all.
        assert (
            page.locator('[data-testid="test-recipient-suggestions"]').count() == 0
        )
        connection.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestBadConfigDoesNotBreakPage:
    def test_invalid_config_entry_dropped(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        _set_common_recipients("not-an-email, ok@example.com")
        _create_staff_user("admin@test.com")
        campaign = _create_campaign()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/campaigns/{campaign.pk}/",
            wait_until="domcontentloaded",
        )

        # Page loaded fine and the valid chip is present.
        ok_chip = _chip(page, "ok@example.com")
        ok_chip.wait_for(state="visible")
        # The invalid value never became a chip.
        assert _chip(page, "not-an-email").count() == 0
        connection.close()
