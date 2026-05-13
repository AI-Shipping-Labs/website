"""
Playwright E2E test for campaign-level contact-tag targeting (issue #357).

Single end-to-end scenario: an operator narrows a draft campaign by an
include tag, saves, and watches the Eligible Recipients count plus the
detail-page filter summary refresh on save.

Other behaviours (form parsing, model semantics, duplicate-copies-tags)
are covered by Django TestCases in ``email_app/tests/test_campaigns.py``
per the testing guidelines.
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
from django.db import connection  # noqa: E402


def _clear_campaigns():
    from email_app.models import EmailCampaign, EmailLog

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    connection.close()


def _create_campaign_with_min_level(subject, target_min_level):
    from email_app.models import EmailCampaign

    campaign = EmailCampaign.objects.create(
        subject=subject,
        body="Body content",
        status="draft",
        target_min_level=target_min_level,
    )
    connection.close()
    return campaign


def _tag_user(email, tags):
    """Set ``user.tags`` directly so the include-tag filter has something to bite."""
    from accounts.models import User

    user = User.objects.get(email=email)
    user.tags = tags
    user.save(update_fields=["tags"])
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestCampaignIncludeTagNarrowsRecipients:
    """An operator scopes a Main+ campaign to ``early-adopter`` and the
    detail page reflects the filter immediately on save."""

    def test_include_tag_narrows_recipient_count_and_renders_filter(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")

        # Three Main-tier verified, subscribed users; only Alice and Bob
        # carry the early-adopter tag.
        _create_user(
            "alice@test.com", tier_slug="main",
            email_verified=True, unsubscribed=False,
        )
        _create_user(
            "bob@test.com", tier_slug="main",
            email_verified=True, unsubscribed=False,
        )
        _create_user(
            "carol@test.com", tier_slug="main",
            email_verified=True, unsubscribed=False,
        )
        _tag_user("alice@test.com", ["early-adopter"])
        _tag_user("bob@test.com", ["early-adopter"])

        campaign = _create_campaign_with_min_level(
            "Cohort update", target_min_level=20,
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # 1. Detail page: all 3 Main-tier verified users count as eligible
        # because the campaign has no tag filter yet.
        detail_url = f"{django_server}/studio/campaigns/{campaign.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")
        eligible = page.locator('[data-testid="eligible-recipients"]')
        assert eligible.inner_text().strip() == "3"

        # The filter summary cells render — but with em-dash placeholders
        # because no tag filter is active.
        include_cell = page.locator('[data-testid="campaign-include-tags"]')
        exclude_cell = page.locator('[data-testid="campaign-exclude-tags"]')
        assert include_cell.inner_text().strip() == "—"
        assert exclude_cell.inner_text().strip() == "—"

        # The action button reflects the current count.
        send_btn = page.locator('[data-testid="send-campaign-btn"]')
        assert "Send to 3 recipients" in send_btn.inner_text()

        # 2. Click Edit, narrow with the include-tag input, save.
        page.locator('[data-testid="edit-campaign-link"]').click()
        page.wait_for_load_state("domcontentloaded")

        include_input = page.locator('[data-testid="target-tags-any-input"]')
        include_input.fill("early-adopter")

        # Submit the form. The form has no test-id so click the Save
        # Changes submit button explicitly.
        page.locator('button[type="submit"]:has-text("Save Changes")').click()
        page.wait_for_load_state("domcontentloaded")

        # 3. We land back on the detail page; eligible count is now 2
        # (Alice and Bob), and the include cell shows the active tag.
        assert "/studio/campaigns/" in page.url
        eligible = page.locator('[data-testid="eligible-recipients"]')
        assert eligible.inner_text().strip() == "2"

        include_cell = page.locator('[data-testid="campaign-include-tags"]')
        exclude_cell = page.locator('[data-testid="campaign-exclude-tags"]')
        assert include_cell.inner_text().strip() == "early-adopter"
        # Exclude is still empty -> em-dash.
        assert exclude_cell.inner_text().strip() == "—"

        # The Send-to button's number is in sync with the recipient count.
        send_btn = page.locator('[data-testid="send-campaign-btn"]')
        assert "Send to 2 recipients" in send_btn.inner_text()
