"""Header/footer pricing and FAQ navigation."""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.mark.django_db(transaction=True)
class TestLoggedInUserMembershipNavigation:
    """The removed header Membership link does not affect pricing access."""

    def test_header_no_longer_has_membership_link_and_pricing_still_loads(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _create_user("nav-membership@test.com", tier_slug="free")

        ctx = _auth_context(browser, "nav-membership@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        assert page.get_by_test_id("nav-membership").count() == 0
        page.get_by_test_id("nav-community-trigger").hover()
        assert page.get_by_test_id("nav-community-link-membership").is_visible()
        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")

        assert page.url.rstrip("/").endswith("/pricing"), (
            f"Expected to land on /pricing, got {page.url}"
        )

        # The four tier names must be visible.
        body_text = page.locator("body").inner_text()
        for tier_name in ("Free", "Basic", "Main", "Premium"):
            assert tier_name in body_text, (
                f"Tier '{tier_name}' missing on pricing page"
            )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestLoggedInUserFaqNavigation:
    """A logged-in user can reach the standalone FAQ page from the footer."""

    def test_footer_faq_link_lands_on_standalone_faq_page(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _create_user("nav-faq@test.com", tier_slug="free")

        ctx = _auth_context(browser, "nav-faq@test.com")
        page = ctx.new_page()
        # Start on the dashboard.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        # Click the footer FAQ link.
        footer_faq = page.locator("footer a", has_text="FAQ")
        footer_faq.scroll_into_view_if_needed()
        footer_faq.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/faq"), (
            f"Expected to land on /faq, got {page.url}"
        )

        body_text = page.locator("body").inner_text()
        assert "Who is this community for?" in body_text, (
            "Expected FAQ questions to render on /faq"
        )

        ctx.close()
