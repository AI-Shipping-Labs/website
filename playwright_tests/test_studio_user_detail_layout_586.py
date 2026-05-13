"""Playwright E2E for the Studio user detail page layout (issue #586).

Covers the 10 scenarios from the groomed spec:

1. Operator finds the primary actions directly under the user header.
2. Operator impersonates a user from the consolidated action row.
3. Operator opens the canonical Django admin page from the action row.
4. Profile reads as a single full-width section above Membership.
5. Operator sees Slack ID as read-only with an Open in Slack link.
6. Operator sees a clear path to edit Slack ID when missing.
7. Operator grants a temporary upgrade from a dedicated section.
8. Operator revokes an active override from the dedicated section.
9. Highest-tier user sees the section state, not the form.
10. Override history link still routes to the per-user overrides page.
11. Page structure has Profile, Membership, Grant upgrade, Tags, CRM in order.

Usage:
    uv run pytest playwright_tests/test_studio_user_detail_layout_586.py -v
"""

import os
from datetime import timedelta

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
)
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
from django.utils import timezone  # noqa: E402

SLACK_KEY = "SLACK_TEAM_ID"


def _reset_state(staff_email):
    """Drop every non-staff user + clear Slack settings so each test
    starts from a deterministic state."""
    from accounts.models import TierOverride, User
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    TierOverride.objects.all().delete()
    User.objects.exclude(email=staff_email).delete()
    IntegrationSetting.objects.filter(key=SLACK_KEY).delete()
    clear_config_cache()
    connection.close()


def _set_team_id(value):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key=SLACK_KEY,
        defaults={
            "value": value,
            "group": "slack",
            "is_secret": False,
            "description": "",
        },
    )
    clear_config_cache()
    connection.close()


def _create_member(
    email,
    tier_slug="free",
    slack_user_id="",
    stripe_customer_id="",
    first_name="",
    last_name="",
    last_login=None,
):
    """Create a member with optional Slack/Stripe fields."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    tier = Tier.objects.get(slug=tier_slug)
    user = User.objects.create_user(
        email=email,
        password=DEFAULT_PASSWORD,
        email_verified=True,
    )
    user.tier = tier
    if slack_user_id:
        user.slack_user_id = slack_user_id
    if stripe_customer_id:
        user.stripe_customer_id = stripe_customer_id
    if first_name:
        user.first_name = first_name
    if last_name:
        user.last_name = last_name
    if last_login is not None:
        user.last_login = last_login
    user.save()
    pk = user.pk
    connection.close()
    return pk


def _make_override(email, tier_slug, granted_by_email, days=30):
    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=email)
    tier = Tier.objects.get(slug=tier_slug)
    granted_by = User.objects.get(email=granted_by_email)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=timezone.now() + timedelta(days=days),
        granted_by=granted_by,
        is_active=True,
    )
    connection.close()


def _section_top(page, testid):
    """Return the bounding-box top coordinate for a section by testid.

    Used for DOM-order assertions that survive responsive layout changes.
    Falls back to -1 when the element is missing (so assertion failures
    point at the testid rather than crashing on a missing box).
    """
    locator = page.locator(f'[data-testid="{testid}"]')
    if locator.count() == 0:
        return -1
    box = locator.bounding_box()
    return box["y"] if box else -1


@pytest.mark.django_db(transaction=True)
class TestUserDetailLayout586:
    # ---------------- Scenario 1 --------------------------------------------

    def test_action_row_directly_under_header_with_two_buttons(
        self, django_server, browser,
    ):
        staff_email = "layout-1-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk = _create_member("layout1@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # H1 carries the email.
        h1 = page.locator("h1[data-testid='user-detail-email']")
        assert h1.count() == 1
        assert "layout1@test.com" in h1.inner_text()

        # Action row sits below the header and contains exactly two
        # controls: Login as user (primary), View in Django admin
        # (secondary). The duplicate "View as user" button is gone.
        actions = page.locator('[data-testid="user-detail-actions"]')
        assert actions.is_visible()
        impersonate = actions.locator(
            '[data-testid="user-detail-impersonate"]'
        )
        admin = actions.locator(
            '[data-testid="user-detail-django-admin"]'
        )
        assert impersonate.count() == 1
        assert admin.count() == 1
        assert "Login as user" in impersonate.inner_text()
        assert "View in Django admin" in admin.inner_text()

        # The previously-rendered duplicate is removed page-wide.
        assert page.locator(
            '[data-testid="user-detail-view-as"]'
        ).count() == 0
        assert "View as user" not in page.content()

        # Action row sits below the header in the DOM.
        header_top = _section_top(page, "user-detail-header")
        actions_top = _section_top(page, "user-detail-actions")
        assert header_top >= 0
        assert actions_top > header_top
        context.close()

    # ---------------- Scenario 2 --------------------------------------------

    def test_login_as_user_impersonates_from_action_row(
        self, django_server, browser,
    ):
        staff_email = "layout-2-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk = _create_member("layout2@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Click the Login as user button. The impersonate endpoint
        # redirects the staff session to '/' as the target user.
        page.locator(
            '[data-testid="user-detail-impersonate"]'
        ).click()
        page.wait_for_url(f"{django_server}/")
        # Confirm the session is now the target user by hitting the
        # account page and reading the identity it serves.
        page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )
        assert "layout2@test.com" in page.content()
        context.close()

    # ---------------- Scenario 3 --------------------------------------------

    def test_view_in_django_admin_lands_on_change_page(
        self, django_server, browser,
    ):
        staff_email = "layout-3-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk = _create_member("layout3@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        admin_link = page.locator(
            '[data-testid="user-detail-django-admin"]'
        )
        assert (
            admin_link.get_attribute("href")
            == f"/admin/accounts/user/{member_pk}/change/"
        )
        admin_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert (
            f"/admin/accounts/user/{member_pk}/change/" in page.url
        )
        context.close()

    # ---------------- Scenario 4 --------------------------------------------

    def test_profile_section_full_width_above_membership(
        self, django_server, browser,
    ):
        staff_email = "layout-4-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk = _create_member(
            "layout4@test.com",
            first_name="Lay",
            last_name="Out",
            stripe_customer_id="cus_LAYOUT4",
            last_login=timezone.now(),
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        profile = page.locator(
            '[data-testid="user-detail-profile-section"]'
        )
        membership = page.locator(
            '[data-testid="user-detail-membership-section"]'
        )
        assert profile.is_visible()
        assert membership.is_visible()

        # Profile sits ABOVE Membership (no side-by-side grid). At lg
        # widths the previous layout placed them in two columns at the
        # same y; now Membership starts strictly below Profile.
        profile_box = profile.bounding_box()
        membership_box = membership.bounding_box()
        assert profile_box is not None
        assert membership_box is not None
        # Membership top must be at or below Profile bottom.
        assert membership_box["y"] >= profile_box["y"] + profile_box["height"] - 2

        # Profile shows Email, Name, Joined, Last login, and Stripe rows.
        profile_text = profile.inner_text()
        assert "Email" in profile_text
        assert "layout4@test.com" in profile_text
        assert "Name" in profile_text
        assert "Lay Out" in profile_text
        assert "Joined" in profile_text
        assert "Last login" in profile_text
        assert "Stripe" in profile_text
        assert "cus_LAYOUT4" in profile_text

        # Sync from Stripe button lives inside the Profile section.
        sync_btn = profile.locator('[data-testid="sync-from-stripe"]')
        assert sync_btn.is_visible()
        context.close()

    # ---------------- Scenario 5 --------------------------------------------

    def test_slack_id_read_only_with_open_in_slack_link(
        self, django_server, browser,
    ):
        staff_email = "layout-5-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        _set_team_id("T01TEAM123")
        member_pk = _create_member(
            "layout5@test.com", slack_user_id="U01ABC123",
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Slack ID rendered as text.
        value_el = page.locator(
            '[data-testid="user-detail-slack-id-value"]'
        )
        assert value_el.is_visible()
        assert "U01ABC123" in value_el.inner_text()

        # Open in Slack anchor with target="_blank".
        slack_link = page.locator(
            '[data-testid="user-detail-slack-profile-link"]'
        )
        assert slack_link.is_visible()
        assert slack_link.get_attribute("target") == "_blank"

        # No <input name="slack_user_id">, no submit, no form posting
        # to the slack-id-set endpoint.
        assert page.locator(
            'input[name="slack_user_id"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="user-detail-slack-id-submit"]'
        ).count() == 0
        assert page.locator(
            f'form[action="/studio/users/{member_pk}/slack-id/"]'
        ).count() == 0
        context.close()

    # ---------------- Scenario 6 --------------------------------------------

    def test_unlinked_slack_id_shows_admin_edit_link(
        self, django_server, browser,
    ):
        staff_email = "layout-6-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk = _create_member("layout6@test.com")  # no slack id

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # "Not linked" pill visible.
        empty_el = page.locator(
            '[data-testid="user-detail-slack-id-empty"]'
        )
        assert empty_el.is_visible()
        assert "Not linked" in empty_el.inner_text()

        # Edit in Django admin link points at the user's change page.
        admin_link = page.locator(
            '[data-testid="user-detail-slack-id-admin-link"]'
        )
        assert admin_link.is_visible()
        assert "Edit in Django admin" in admin_link.inner_text()
        assert (
            admin_link.get_attribute("href")
            == f"/admin/accounts/user/{member_pk}/change/"
        )
        context.close()

    # ---------------- Scenario 7 --------------------------------------------

    def test_grant_temporary_upgrade_section_grants_override(
        self, django_server, browser,
    ):
        staff_email = "layout-7-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        _create_user("layout7@test.com", tier_slug="free")
        from accounts.models import User

        member_pk = User.objects.get(email="layout7@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Standalone Grant temporary upgrade section between Membership
        # and Tags.
        section = page.locator(
            '[data-testid="user-detail-tier-override-section"]'
        )
        assert section.is_visible()
        membership_top = _section_top(page, "user-detail-membership-section")
        section_top = _section_top(page, "user-detail-tier-override-section")
        tags_top = _section_top(page, "user-tags-section")
        assert membership_top < section_top < tags_top

        # Section h2 carries the title.
        h2 = section.locator("h2")
        assert "Grant temporary upgrade" in h2.inner_text()

        # At least one tier radio + one duration button visible.
        tier_options = section.locator(
            '[data-testid="user-detail-tier-override-tier-option"]'
        )
        durations = section.locator(
            '[data-testid="user-detail-tier-override-duration"]'
        )
        assert tier_options.count() >= 1
        assert durations.count() >= 1

        # Pick Basic and click 14 days. (The spec text mentions a "1 week"
        # button but the canonical DURATION_CHOICES list starts at
        # "14 days"; we use the real first option.)
        page.locator(
            'input[name="tier_id"][data-tier-slug="basic"]'
        ).check(force=True)
        page.locator(
            '[data-testid="user-detail-tier-override-duration"]'
            '[data-duration="14 days"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")

        # Membership tier row now carries the Override badge with the
        # granted tier name.
        tier_cell = page.locator('[data-testid="user-detail-tier"]')
        assert "Basic (override)" in tier_cell.inner_text()
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute("data-tier-source") == "override"
        context.close()

    # ---------------- Scenario 8 --------------------------------------------

    def test_revoke_active_override_from_dedicated_section(
        self, django_server, browser,
    ):
        staff_email = "layout-8-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        _create_user("layout8@test.com", tier_slug="free")
        _make_override("layout8@test.com", "main", staff_email, days=7)
        from accounts.models import User

        member_pk = User.objects.get(email="layout8@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Active override summary visible inside the dedicated section.
        section = page.locator(
            '[data-testid="user-detail-tier-override-section"]'
        )
        section_text = section.inner_text()
        assert "Active override to" in section_text
        assert "Main" in section_text
        # Tier row currently carries the Override badge.
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute("data-tier-source") == "override"

        # Click Revoke override and accept the confirm() prompt.
        page.once("dialog", lambda d: d.accept())
        section.locator(
            '[data-testid="user-detail-tier-override-revoke"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")

        # Tier row drops the Override badge.
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute("data-tier-source") in ("default", "stripe")
        context.close()

    # ---------------- Scenario 9 --------------------------------------------

    def test_highest_tier_user_sees_section_state_not_form(
        self, django_server, browser,
    ):
        staff_email = "layout-9-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        _create_user("layout9@test.com", tier_slug="premium")
        from accounts.models import User

        member_pk = User.objects.get(email="layout9@test.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        section = page.locator(
            '[data-testid="user-detail-tier-override-section"]'
        )
        assert section.is_visible()
        # The highest-tier empty state renders.
        highest = section.locator(
            '[data-testid="user-detail-tier-override-highest"]'
        )
        assert highest.count() == 1
        assert "highest tier" in highest.inner_text().lower()

        # No tier-radio inputs and no duration buttons.
        assert section.locator(
            '[data-testid="user-detail-tier-override-tier-option"]'
        ).count() == 0
        assert section.locator(
            '[data-testid="user-detail-tier-override-duration"]'
        ).count() == 0
        context.close()

    # ---------------- Scenario 10 -------------------------------------------

    def test_override_history_link_routes_to_per_user_overrides(
        self, django_server, browser,
    ):
        staff_email = "layout-10-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        _create_user("m@example.com", tier_slug="free")
        from accounts.models import User

        member_pk = User.objects.get(email="m@example.com").pk
        connection.close()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # The history link sits inside the dedicated override section.
        section = page.locator(
            '[data-testid="user-detail-tier-override-section"]'
        )
        link = section.locator(
            '[data-testid="user-detail-tier-override-history-link"]'
        )
        assert link.count() == 1
        link.click()
        page.wait_for_load_state("domcontentloaded")
        # Either the literal email or the urlencoded form is acceptable
        # (the template currently passes the raw email through; both
        # forms address the same record).
        assert f"/studio/users/{member_pk}/tier_override/" in page.url
        context.close()

    # ---------------- Scenario 11 -------------------------------------------

    def test_section_dom_order_profile_membership_grant_tags_crm(
        self, django_server, browser,
    ):
        # The page renders the five top-level section cards in the
        # documented order. Uses bounding-box y values so the test
        # survives Tailwind class shuffles.
        staff_email = "layout-11-admin@test.com"
        _create_staff_user(staff_email)
        _reset_state(staff_email)
        member_pk = _create_member("layout11@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        order = [
            "user-detail-profile-section",
            "user-detail-membership-section",
            "user-detail-tier-override-section",
            "user-tags-section",
            "user-crm-section",
        ]
        ys = [_section_top(page, testid) for testid in order]
        # Every section is present and the y values strictly increase.
        for testid, y in zip(order, ys):
            assert y >= 0, f"Section {testid} not rendered"
        for prev, nxt in zip(ys, ys[1:]):
            assert prev < nxt, f"Section order broken at {ys}"
        context.close()
