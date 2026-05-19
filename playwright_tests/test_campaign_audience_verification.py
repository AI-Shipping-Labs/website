"""
Playwright E2E for ``audience_verification`` selector (issue #692).

Single scenario: an operator creates a campaign with the default
``verified_only`` audience, observes the verified-only recipient count,
edits the campaign to ``everyone``, and watches the recipient count
grow on the detail page. The edit form then re-renders the warning
block with the canonical copy.

Other behaviors (model semantics, normalization, API round-trip) are
covered by Django TestCases per the testing guidelines.
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


@pytest.mark.django_db(transaction=True)
class TestAudienceVerificationToggle:
    """Switch a campaign from verified_only to everyone and see the warning.

    Fixture: 2 verified non-unsubscribed users and 2 unverified
    non-unsubscribed users at the same (free) tier (plus the staff
    admin@test.com, who is also verified). Switching from
    ``verified_only`` to ``everyone`` adds the 2 unverified users to the
    audience, so the diff is what we pin.
    """

    def test_switch_to_everyone_widens_audience_and_shows_warning(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")

        _create_user(
            "v1@test.com", tier_slug="free",
            email_verified=True, unsubscribed=False,
        )
        _create_user(
            "v2@test.com", tier_slug="free",
            email_verified=True, unsubscribed=False,
        )
        _create_user(
            "u1@test.com", tier_slug="free",
            email_verified=False, unsubscribed=False,
        )
        _create_user(
            "u2@test.com", tier_slug="free",
            email_verified=False, unsubscribed=False,
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # 1. New campaign form: selector defaults to "Verified only" and
        # the warning block is absent.
        page.goto(
            f"{django_server}/studio/campaigns/new",
            wait_until="domcontentloaded",
        )
        selector = page.locator(
            '[data-testid="campaign-audience-verification"]',
        )
        assert selector.input_value() == "verified_only"
        warning = page.locator(
            '[data-testid="campaign-audience-verification-warning"]',
        )
        assert warning.count() == 0

        # 2. Fill subject/body, keep default selector, submit.
        page.locator('input[name="subject"]').fill("Audience toggle E2E")
        page.locator('textarea[name="body"]').fill("Hello world body")
        page.locator(
            'button[type="submit"]:has-text("Save as Draft")',
        ).click()
        page.wait_for_load_state("domcontentloaded")

        # On the detail page, capture the verified-only recipient count.
        eligible = page.locator('[data-testid="eligible-recipients"]')
        verified_only_count = int(eligible.inner_text().strip())

        detail_url = page.url

        # 3. Click Edit, change selector to "everyone", save.
        page.locator('[data-testid="edit-campaign-link"]').click()
        page.wait_for_load_state("domcontentloaded")

        page.locator(
            '[data-testid="campaign-audience-verification"]',
        ).select_option("everyone")
        page.locator(
            'button[type="submit"]:has-text("Save Changes")',
        ).click()
        page.wait_for_load_state("domcontentloaded")

        # Back on the detail page, the count is strictly greater than before
        # and grows by exactly the two newly-included unverified users.
        eligible = page.locator('[data-testid="eligible-recipients"]')
        everyone_count = int(eligible.inner_text().strip())
        assert everyone_count > verified_only_count
        assert everyone_count - verified_only_count == 2

        # 4. Navigate back to the edit form: selector is "everyone" and
        # the warning block renders with the canonical copy.
        page.goto(
            detail_url.rstrip("/") + "/edit",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="campaign-audience-verification"]',
        ).input_value() == "everyone"
        warning = page.locator(
            '[data-testid="campaign-audience-verification-warning"]',
        )
        assert warning.count() == 1
        assert (
            "sending to unverified addresses may hurt deliverability"
            in warning.inner_text().lower()
        )
