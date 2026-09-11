"""Browser journeys for daily Slack channel reconciliation (#1572)."""

import os
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.creates_data,
    pytest.mark.core,
]


def _create_member(email, *, tier_slug="main", is_staff=False):
    from accounts.models import User
    from payments.models import Tier

    ensure_tiers()
    user = User.objects.create_user(
        email=email,
        password="pw",
        tier=Tier.objects.get(slug=tier_slug),
        is_staff=is_staff,
    )
    connection.close()
    return user.pk


def _service(results):
    service = MagicMock()
    service.channel_ids = ["C_ONE", "C_TWO"]
    service.check_workspace_membership.return_value = ("member", "U1572")
    service.lookup_user_profile_by_email.return_value = None
    service.add_to_channels.return_value = results
    return service


class TestIssue1572SlackChannelReconciliation:
    @browser_journey
    def test_main_account_copy_and_join_redirect(self, django_server, browser, settings):
        email = "account-1572@test.com"
        _create_member(email)
        settings.SLACK_INVITE_URL = "https://join.slack.test/1572"
        context = auth_context(browser, email)
        page = context.new_page()
        page.goto(f"{django_server}/account/")

        expect(page.get_by_test_id("slack-account-card")).to_contain_text(
            "Already joined? We check Slack daily and add members with community access "
            "to the community channels automatically."
        )
        expect(page.get_by_test_id("slack-account-card-join")).to_have_attribute(
            "href", "/community/slack"
        )
        redirect = context.request.get(
            f"{django_server}/community/slack", max_redirects=0,
        )
        assert redirect.status == 302
        assert redirect.headers["location"] == "https://join.slack.test/1572"
        context.close()

    @browser_journey
    def test_staff_complete_result(self, django_server, browser):
        staff_email = "staff-complete-1572@test.com"
        create_staff_user(staff_email)
        member_pk = _create_member("complete-1572@test.com")
        service = _service([
            {"channel": "C_ONE", "ok": True},
            {"channel": "C_TWO", "ok": True, "already_in": True},
        ])
        context = auth_context(browser, staff_email)
        page = context.new_page()
        with patch("community.tasks.slack_membership.get_community_service", return_value=service):
            page.goto(f"{django_server}/studio/users/{member_pk}/")
            page.get_by_test_id("user-detail-slack-check").click()
            expect(page.get_by_text(
                "Slack membership checked: Member. Community channels are connected."
            )).to_be_visible()
        service.add_to_channels.assert_called_once_with("U1572")
        context.close()

    @browser_journey
    def test_staff_partial_result(self, django_server, browser):
        staff_email = "staff-partial-1572@test.com"
        create_staff_user(staff_email)
        member_pk = _create_member("partial-1572@test.com")
        service = _service([
            {"channel": "C_ONE", "ok": True},
            {"channel": "C_TWO", "ok": False, "error": "not_allowed"},
        ])
        context = auth_context(browser, staff_email)
        page = context.new_page()
        with patch("community.tasks.slack_membership.get_community_service", return_value=service):
            page.goto(f"{django_server}/studio/users/{member_pk}/")
            page.get_by_test_id("user-detail-slack-check").click()
            expect(page.get_by_text(
                "Slack membership checked: Member, but community channels could not "
                "be fully connected. Check the Slack integration and try again."
            )).to_be_visible()
        context.close()

    @browser_journey
    def test_nonstaff_studio_and_api_denials_are_side_effect_free(
        self, django_server, browser,
    ):
        regular = "regular-1572@test.com"
        _create_member(regular, tier_slug="free")
        member_pk = _create_member("protected-1572@test.com")
        context = auth_context(browser, regular)
        page = context.new_page()

        page.goto(f"{django_server}/account/")
        response = page.goto(f"{django_server}/studio/users/{member_pk}/")
        assert response.status == 403
        api_response = context.request.post(
            f"{django_server}/api/users/protected-1572@test.com/slack-membership/check"
        )
        assert api_response.status == 401
        assert api_response.json()["code"] == "authentication_required"

        from accounts.models import User
        protected = User.objects.get(pk=member_pk)
        assert protected.slack_checked_at is None
        assert protected.slack_member is False
        connection.close()
        context.close()
