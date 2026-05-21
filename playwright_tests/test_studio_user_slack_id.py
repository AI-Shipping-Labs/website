"""Playwright E2E for the Studio Slack ID surface (issue #561).

Covers the remaining scenarios from the groomed spec after #451
narrowed Slack deep-linking to the user detail page:

1. Operator finds a member by pasting their Slack ID into the users-list
   search box.
2. Operator jumps from the user detail page straight to Slack via the
   "Open in Slack" link.
3. Slack profile is unreachable (no anchor) when ``SLACK_TEAM_ID`` is not
   configured.
4. Operator links a member to Slack by typing the ID manually, including
   the invalid-format flash on a retry.
5. Operator clears a wrongly-linked Slack ID (empty submit clears the
   row).
6. Non-staff users cannot reach the slack-id edit endpoint (POST is
   rejected and the DB is unchanged).
7. (Deleted in #451) Per-row Slack anchor on the listing -- the listing
   surfaces Slack ID via the row's ``<tr title>`` tooltip now; the
   clickable "Open in Slack" link is detail-page only (Scenario 2).
8. Operator updates the team ID once and the member detail page's
   "Open in Slack" link starts working without per-user edits.

Usage:
    uv run pytest playwright_tests/test_studio_user_slack_id.py -v
"""

import os

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
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

SETTINGS_KEY = "SLACK_TEAM_ID"


def _reset_users_and_settings(staff_email):
    """Drop every non-staff user and clear ``SLACK_TEAM_ID`` so each test
    starts from a deterministic state."""
    from accounts.models import User
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    User.objects.exclude(email=staff_email).delete()
    IntegrationSetting.objects.filter(key=SETTINGS_KEY).delete()
    clear_config_cache()
    connection.close()


def _set_team_id(value):
    """Persist a team ID via ``IntegrationSetting`` + clear the cache."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key=SETTINGS_KEY,
        defaults={
            "value": value,
            "group": "slack",
            "is_secret": False,
            "description": "Workspace team ID for deep-links.",
        },
    )
    clear_config_cache()
    connection.close()


def _create_member(email, slack_user_id=""):
    """Create a free member with an optional ``slack_user_id``."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    tier = Tier.objects.get(slug="free")
    user = User.objects.create_user(
        email=email,
        password=DEFAULT_PASSWORD,
        email_verified=True,
    )
    user.tier = tier
    if slack_user_id:
        user.slack_user_id = slack_user_id
    user.save()
    pk = user.pk
    connection.close()
    return pk


def _read_user_field(email, field):
    from accounts.models import User

    value = getattr(User.objects.get(email=email), field)
    connection.close()
    return value


@pytest.mark.django_db(transaction=True)
class TestStudioUserSlackId:
    # ---------------- Scenario 1 --------------------------------------------

    def test_search_finds_user_by_pasted_slack_id(self, django_server, browser):
        staff_email = "slack-search-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        _create_member("ada@example.com", slack_user_id="U01ADA123")
        _create_member("grace@example.com", slack_user_id="U02GRACE9")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # 1. Type the exact Slack ID into the search box and submit.
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        # The users list renders TWO inputs named ``q``: the visible
        # search box (``type="text"``) and a hidden mirror inside the
        # tag-picker form (issue #694) that only appears after the page
        # is loaded with a ``?q=...`` query param. Disambiguate with the
        # ``type`` attribute so Playwright's strict locator matches only
        # the visible search box on every step.
        page.locator("input[type='text'][name='q']").fill("U01ADA123")
        page.locator("button:has-text('Search')").click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Ada is in the list, Grace is not.
        body = page.content()
        assert "ada@example.com" in body
        assert "grace@example.com" not in body

        # 2. Clear, paste a lowercase substring of Grace's Slack ID.
        search_box = page.locator("input[type='text'][name='q']")
        search_box.fill("u02grace")
        page.locator("button:has-text('Search')").click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Grace is in the list (case-insensitive search), Ada is not.
        body = page.content()
        assert "grace@example.com" in body
        assert "ada@example.com" not in body
        context.close()

    # ---------------- Scenario 2 --------------------------------------------

    def test_open_in_slack_link_from_detail_page(self, django_server, browser):
        staff_email = "slack-detail-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        _set_team_id("T01TEAM123")
        member_pk = _create_member("ada@example.com", slack_user_id="U01ADA123")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Slack ID row is present with the value in monospace.
        slack_row = page.locator('[data-testid="user-detail-slack-id-row"]')
        assert slack_row.is_visible()
        value_el = page.locator('[data-testid="user-detail-slack-id-value"]')
        assert value_el.is_visible()
        assert "U01ADA123" in value_el.inner_text()
        # ``font-mono`` class drives the monospace appearance.
        assert "font-mono" in (value_el.get_attribute("class") or "")

        # The "Open in Slack" anchor points at the canonical web URL and
        # opens in a new tab.
        link = page.locator('[data-testid="user-detail-slack-profile-link"]')
        assert link.is_visible()
        assert (
            link.get_attribute("href")
            == "https://app.slack.com/client/T01TEAM123/U01ADA123"
        )
        assert link.get_attribute("target") == "_blank"
        assert (link.get_attribute("rel") or "").lower().find("noopener") != -1
        context.close()

    # ---------------- Scenario 3 --------------------------------------------

    def test_slack_link_absent_when_team_id_not_configured(
        self, django_server, browser,
    ):
        staff_email = "slack-no-team-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)  # team id intentionally blank
        member_pk = _create_member("ada@example.com", slack_user_id="U01ADA123")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # ID is rendered in monospace as plain text — anchor must be missing.
        value_el = page.locator('[data-testid="user-detail-slack-id-value"]')
        assert value_el.is_visible()
        assert "U01ADA123" in value_el.inner_text()
        assert (
            page.locator('[data-testid="user-detail-slack-profile-link"]').count()
            == 0
        )
        # Tooltip on the plain-text span explains why the link is missing.
        assert (
            value_el.get_attribute("title")
            == "Configure SLACK_TEAM_ID to enable the link"
        )

        # Issue #451 removed the per-row Slack pill from /studio/users/.
        # The Slack ID still surfaces via the row-level ``<tr title>``
        # tooltip, and the row is not made clickable as a Slack anchor
        # (that lives on the user detail page now). Confirm the tooltip
        # carries the ID even though the team_id is unset -- the missing
        # team_id only suppresses the detail-page anchor.
        page.goto(
            f"{django_server}/studio/users/?q=ada@example.com",
            wait_until="domcontentloaded",
        )
        row = page.locator(f'[data-testid="user-row-{member_pk}"]')
        assert row.is_visible()
        tooltip = row.get_attribute("title") or ""
        assert "Slack ID: U01ADA123" in tooltip
        # The deleted per-row glyphs from the old Membership column must
        # not reappear -- those have moved to the detail page / tooltip.
        assert row.locator('[data-testid="slack-status"]').count() == 0
        assert row.locator('[data-testid="slack-profile-link"]').count() == 0
        context.close()

    # ---------------- Scenario 4 --------------------------------------------

    def test_unlinked_row_offers_django_admin_path(
        self, django_server, browser,
    ):
        # Issue #586 removed the inline edit form from the user detail
        # page. When the Slack ID is missing, the row now shows
        # "Not linked" plus an "Edit in Django admin" link so operators
        # still have a one-click path forward. The slack-id POST
        # endpoint stays callable from Django admin / scripts.
        staff_email = "slack-manual-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        _set_team_id("T01TEAM123")
        member_pk = _create_member("partner@example.com")  # no slack id yet

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Empty state: "Not linked" pill + "Edit in Django admin" link
        # pointing at the canonical change page.
        assert page.locator(
            '[data-testid="user-detail-slack-id-empty"]'
        ).is_visible()
        admin_link = page.locator(
            '[data-testid="user-detail-slack-id-admin-link"]'
        )
        assert admin_link.is_visible()
        assert (
            admin_link.get_attribute("href")
            == f"/admin/accounts/user/{member_pk}/change/"
        )

        # The inline edit form, input, and submit must all be gone.
        assert page.locator(
            '[data-testid="user-detail-slack-id-form"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="user-detail-slack-id-input"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="user-detail-slack-id-submit"]'
        ).count() == 0
        # Helper copy about the expected ID format also gone.
        assert "Set Slack ID" not in page.content()

        # The slack-id POST endpoint contract (route stays defined +
        # writes the new value when called from Django admin / scripts)
        # is covered by the Django unit test
        # ``SlackIdSetEndpointStillCallableTest.test_post_writes_value_through_endpoint``
        # in ``studio/tests/test_user_detail_layout_586.py``. Playwright
        # focuses on the rendered detail page only.
        context.close()

    # ---------------- Scenario 5 --------------------------------------------

    def test_linked_row_renders_value_with_no_inline_edit_controls(
        self, django_server, browser,
    ):
        # Issue #586: when a Slack ID is already set, the row renders
        # the value (and "Open in Slack" anchor when configured) but no
        # input, no save button, no admin-edit link.
        staff_email = "slack-clear-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        _set_team_id("T01TEAM123")
        member_pk = _create_member("ghost@example.com", slack_user_id="U99WRONG")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # ID renders with the Open in Slack anchor.
        value_el = page.locator('[data-testid="user-detail-slack-id-value"]')
        assert "U99WRONG" in value_el.inner_text()
        assert page.locator(
            '[data-testid="user-detail-slack-profile-link"]'
        ).is_visible()

        # Inline edit controls and the admin-edit link (which only shows
        # when the row is empty) are absent.
        for testid in (
            "user-detail-slack-id-form",
            "user-detail-slack-id-input",
            "user-detail-slack-id-submit",
            "user-detail-slack-id-admin-link",
        ):
            assert page.locator(f'[data-testid="{testid}"]').count() == 0
        context.close()

    # ---------------- Scenario 6 --------------------------------------------

    def test_non_staff_cannot_post_to_slack_id_endpoint(
        self, django_server, browser,
    ):
        from playwright_tests.conftest import create_user as _create_user

        staff_email = "slack-acl-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        member_pk = _create_member("target@example.com")
        # Free, non-staff member who attempts the privileged endpoint.
        _create_user("member@example.com", tier_slug="free", is_staff=False)

        # Sign in as the non-staff user.
        context = _auth_context(browser, "member@example.com")
        page = context.new_page()
        # Visit the target detail page first to get a CSRF cookie issued
        # to this session. We don't expect a 403 on GETting the detail
        # page because @staff_required for user_detail will redirect /
        # 403; we instead reach the endpoint via a direct POST.
        page.goto(
            f"{django_server}/studio/users/{member_pk}/slack-id/",
            wait_until="domcontentloaded",
        )

        # @staff_required on a GET returns 405 for the POST-only view
        # AFTER the auth gate runs. To exercise the auth gate's behavior
        # specifically, fire a POST via the request context.
        csrf_token = (
            "e2e-test-csrf-token-value"  # matches the value set in auth_context
        )
        response = context.request.post(
            f"{django_server}/studio/users/{member_pk}/slack-id/",
            data={
                "slack_user_id": "U99HIJACK",
                "csrfmiddlewaretoken": csrf_token,
            },
            headers={"X-CSRFToken": csrf_token},
        )
        # Either 403 (non-staff authenticated user) or a redirect to the
        # login page (anonymous user) is acceptable per the @staff_required
        # contract. For an authenticated non-staff user we get 403.
        assert response.status in (302, 403), (
            f"Expected 302/403 for non-staff POST, got {response.status}"
        )

        # Database side-effect guard: the target user's slack_user_id
        # MUST be unchanged.
        assert _read_user_field("target@example.com", "slack_user_id") == ""
        context.close()

    # ---------------- Scenario 7 --------------------------------------------
    # Issue #451 removed the per-row Slack profile anchor from the
    # /studio/users/ listing -- the Slack ID now surfaces via the row's
    # ``<tr title>`` tooltip and the clickable "Open in Slack" anchor
    # lives on /studio/users/<id>/ only (covered by Scenario 2 and the
    # detail-page scenarios). The old
    # ``test_list_row_pill_links_directly_to_slack_when_configured`` test
    # was deleted as part of this collapse -- the contract it asserted
    # (``slack-profile-link`` on the listing row) no longer exists.

    # ---------------- Scenario 8 --------------------------------------------

    def test_updating_team_id_once_enables_member_detail_link(
        self, django_server, browser,
    ):
        # Issue #451 narrowed Slack deep-linking to the user detail page.
        # The "operator updates the team ID once and every member link
        # starts working" intent is still meaningful: setting the team ID
        # is what flips every user-detail page's anchor on. We exercise
        # the flow end-to-end with a single representative member
        # (Scenario 2 already locks the detail-page anchor format).
        staff_email = "slack-config-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        ada_pk = _create_member("ada@example.com", slack_user_id="U01ADA123")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # Before saving the team ID: detail page renders the ID but no
        # "Open in Slack" anchor (matches Scenario 3).
        page.goto(
            f"{django_server}/studio/users/{ada_pk}/",
            wait_until="domcontentloaded",
        )
        assert (
            page.locator(
                '[data-testid="user-detail-slack-profile-link"]'
            ).count()
            == 0
        )

        # 1. Visit /studio/settings/.
        page.goto(
            f"{django_server}/studio/settings/",
            wait_until="domcontentloaded",
        )
        # 2. Set SLACK_TEAM_ID to T01TEAM123 and save.
        # The page renders one input per registered key inside the slack
        # group's form; submit via the form helper to avoid disambiguating
        # multiple group save buttons.
        team_id_input = page.locator('input[name="SLACK_TEAM_ID"]')
        assert team_id_input.count() == 1, (
            "Settings dashboard must render an input for SLACK_TEAM_ID"
        )
        team_id_input.fill("T01TEAM123")
        team_id_input.evaluate("el => el.form.submit()")
        page.wait_for_load_state("domcontentloaded")

        # 3. After save, Ada's detail page has the clickable Slack
        #    anchor pointing at the right URL.
        page.goto(
            f"{django_server}/studio/users/{ada_pk}/",
            wait_until="domcontentloaded",
        )
        link = page.locator(
            '[data-testid="user-detail-slack-profile-link"]'
        )
        assert link.count() == 1, (
            "User detail page should expose the Slack anchor after team ID set"
        )
        assert (
            link.get_attribute("href")
            == "https://app.slack.com/client/T01TEAM123/U01ADA123"
        )
        context.close()
