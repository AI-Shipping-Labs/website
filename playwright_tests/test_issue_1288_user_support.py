"""Core operator journeys for the issue #1288 user-support surface."""

import datetime
import os
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]
SCREENSHOTS = Path(".tmp/issue-1288-screenshots")


def _seed(staff_email="support-1288@test.com"):
    from accounts.models import User
    from crm.models import CRMRecord
    from payments.models import Tier

    ensure_tiers()
    create_staff_user(staff_email)
    CRMRecord.objects.all().delete()
    User.objects.exclude(email=staff_email).delete()
    free = Tier.objects.get(slug="free")
    member = User.objects.create_user(
        email="member-1288@test.com", password="pw", tier=free,
        email_verified=True, tags=["support-priority"],
    )
    crm = CRMRecord.objects.create(user=member, created_by=User.objects.get(email=staff_email))
    ids = (member.pk, crm.pk)
    connection.close()
    return ids


def _page(browser, staff_email="support-1288@test.com", *, mobile=False):
    context = auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size({"width": 393, "height": 852} if mobile else {"width": 1280, "height": 900})
    return context, page


@pytest.mark.django_db(transaction=True)
class TestIssue1288UserSupport:
    def test_alias_add_remove_and_conflict(self, django_server, browser):
        from accounts.models import User

        member_pk, _ = _seed()
        other = User.objects.create_user(email="owned-1288@test.com")
        connection.close()
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/users/{member_pk}/")
        page.get_by_test_id("user-alias-input").fill("Relay@Example.com")
        page.get_by_test_id("user-alias-note").fill("Billing relay")
        page.get_by_test_id("user-alias-add-submit").click()
        page.get_by_text("relay@example.com", exact=True).wait_for()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_test_id("user-alias-remove").click()
        page.get_by_text("No aliases yet.").wait_for()
        page.get_by_test_id("user-alias-input").fill(other.email)
        page.get_by_test_id("user-alias-add-submit").click()
        assert page.get_by_text("already a primary account email", exact=False).is_visible()
        context.close()

    def test_check_now_refreshes_slack_and_preserves_id(self, django_server, browser):
        member_pk, _ = _seed()
        service = Mock()
        service.check_workspace_membership.return_value = ("member", "U01ABC123")
        service.get_user_profile.return_value = None
        context, page = _page(browser)
        with patch("community.tasks.slack_membership.get_community_service", return_value=service):
            page.goto(f"{django_server}/studio/users/{member_pk}/")
            page.get_by_test_id("user-detail-slack-check").click()
            page.get_by_text("Slack membership checked: Member.").wait_for()
            assert "U01ABC123" in page.get_by_test_id("user-detail-slack-id-value").inner_text()
            page.get_by_test_id("user-detail-slack-check").click()
            assert "U01ABC123" in page.get_by_test_id("user-detail-slack-id-value").inner_text()
        context.close()

    def test_slack_unknown_preserves_trusted_state(self, django_server, browser):
        from accounts.models import User

        member_pk, _ = _seed()
        user = User.objects.get(pk=member_pk)
        user.slack_member = True
        user.slack_user_id = "U01TRUSTED"
        user.slack_checked_at = timezone.now()
        user.save(update_fields=["slack_member", "slack_user_id", "slack_checked_at"])
        connection.close()
        service = Mock()
        service.check_workspace_membership.return_value = ("unknown", None)
        context, page = _page(browser)
        with patch("community.tasks.slack_membership.get_community_service", return_value=service):
            page.goto(f"{django_server}/studio/users/{member_pk}/")
            page.get_by_test_id("user-detail-slack-check").click()
            page.get_by_text("Slack membership could not be checked. Try again.").wait_for()
            assert page.get_by_text("U01TRUSTED", exact=True).is_visible()
        context.close()

    def test_keyboard_edit_and_clear_slack_id(self, django_server, browser):
        member_pk, _ = _seed()
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/users/{member_pk}/")
        toggle = page.locator("#slack-id-edit-toggle")
        toggle.focus()
        page.keyboard.press("Enter")
        assert page.locator("#slack-user-id").evaluate("el => el === document.activeElement")
        page.locator("#slack-user-id").fill("u01abc123")
        page.get_by_role("button", name="Save").click()
        page.get_by_text("Slack ID set to U01ABC123.").wait_for()
        page.locator("#slack-id-edit-toggle").click()
        page.locator("#slack-user-id").fill("")
        page.get_by_role("button", name="Save").click()
        page.get_by_text("Slack ID cleared.").wait_for()
        context.close()

    def test_cached_subscription_copy(self, django_server, browser):
        from accounts.models import User
        from payments.models import Tier

        member_pk, _ = _seed()
        user = User.objects.get(pk=member_pk)
        user.tier = Tier.objects.get(slug="main")
        user.subscription_id = "sub_1288"
        user.billing_period_end = timezone.now() + datetime.timedelta(days=30)
        user.save(update_fields=["tier", "subscription_id", "billing_period_end"])
        connection.close()
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/users/{member_pk}/")
        summary = page.get_by_test_id("user-detail-subscription")
        assert "Main" in summary.inner_text() and "Renews" in summary.inner_text()
        context.close()

    def test_sort_headers_toggle_preserve_filters_and_announce(self, django_server, browser):
        member_pk, _ = _seed()
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/users/?q=member-1288&tag=support-priority")
        joined = page.get_by_role("link", name="Joined")
        joined.click()
        assert "q=member-1288" in page.url and "tag=support-priority" in page.url
        assert page.locator('th[aria-sort="ascending"]').count() == 1
        page.get_by_role("link", name="Last login").click()
        page.get_by_role("link", name="Last login").click()
        assert page.locator('th[aria-sort="descending"]').count() == 1
        context.close()

    def test_crm_exact_tag_filter_composes(self, django_server, browser):
        from accounts.models import User
        from crm.models import CRMRecord

        _, _ = _seed()
        other = User.objects.create_user(email="other-1288@test.com", tags=["support-priority-vip"])
        CRMRecord.objects.create(user=other)
        connection.close()
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/crm/?filter=active&q=1288")
        page.get_by_test_id("crm-tag-filter").select_option("support-priority")
        page.get_by_text("member-1288@test.com", exact=True).wait_for()
        assert page.get_by_text("other-1288@test.com", exact=True).count() == 0
        context.close()

    def test_note_crud_returns_to_crm_anchor(self, django_server, browser):
        member_pk, crm_pk = _seed()
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/crm/{crm_pk}/")
        page.get_by_role("link", name="Add member note").click()
        page.locator('textarea[name="body"]').fill("Support context")
        page.get_by_role("button", name="Save note").click()
        page.wait_for_url(f"**/studio/crm/{crm_pk}/#member-notes")
        page.get_by_role("link", name="Edit").click()
        page.locator('textarea[name="body"]').fill("")
        page.locator('textarea[name="body"]').evaluate("el => el.removeAttribute('required')")
        page.get_by_role("button", name="Save changes").click()
        page.get_by_text("Note body is required.").wait_for()
        page.locator('textarea[name="body"]').fill("Corrected context")
        page.get_by_role("button", name="Save changes").click()
        page.wait_for_url(f"**/studio/crm/{crm_pk}/#member-notes")
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Delete").click()
        page.wait_for_url(f"**/studio/crm/{crm_pk}/#member-notes")
        page.get_by_text("Member note deleted.").wait_for()
        context.close()

    def test_unsafe_note_next_falls_back_inside_studio(self, django_server, browser):
        member_pk, _ = _seed()
        context, page = _page(browser)
        unsafe_values = (
            "https://evil.example/studio/crm/1",
            "//evil.example/studio/crm/1",
            "\\\\evil.example\\studio\\crm\\1",
            "/pricing",
            "/studio/../../outside",
            "/studio/%2e%2e/%2e%2e/outside",
        )
        for unsafe in unsafe_values:
            page.goto(
                f"{django_server}/studio/users/{member_pk}/notes/new"
                f"?next={quote(unsafe, safe='')}"
            )
            assert page.locator(
                f'a[href="/studio/users/{member_pk}/#member-notes"]'
            ).count() == 2
        page.locator('textarea[name="body"]').fill("Safe")
        page.get_by_role("button", name="Save note").click()
        page.wait_for_url(f"**/studio/users/{member_pk}/#member-notes")
        context.close()

    def test_custom_expiry_and_past_validation(self, django_server, browser):
        member_pk, _ = _seed()
        future = timezone.now().date() + datetime.timedelta(days=14)
        past = timezone.now().date() - datetime.timedelta(days=1)
        context, page = _page(browser)
        page.goto(f"{django_server}/studio/users/{member_pk}/")
        page.get_by_test_id("user-detail-tier-override-expires-at").fill(past.isoformat())
        page.get_by_test_id("user-detail-tier-override-custom").click()
        page.get_by_text("Until date must be today or later.").wait_for()
        page.get_by_test_id("user-detail-tier-override-expires-at").fill(future.isoformat())
        page.get_by_test_id("user-detail-tier-override-custom").click()
        page.get_by_text("until", exact=False).first.wait_for()
        context.close()

    @pytest.mark.manual_visual
    def test_light_dark_desktop_mobile_changed_workflows(self, django_server, browser):
        from accounts.models import EmailAlias, TierOverride, User
        from payments.models import Tier

        member_pk, crm_pk = _seed()
        member = User.objects.get(pk=member_pk)
        staff = User.objects.get(email="support-1288@test.com")
        main = Tier.objects.get(slug="main")
        premium = Tier.objects.get(slug="premium")
        member.tier = main
        member.subscription_id = "sub_1288_visual_long_identifier"
        member.stripe_customer_id = "cus_1288_visual_long_identifier"
        member.billing_period_end = timezone.now() + datetime.timedelta(days=30)
        member.slack_member = True
        member.slack_user_id = "U01VISUAL1288"
        member.slack_checked_at = timezone.now()
        member.save()
        EmailAlias.objects.create(
            user=member,
            email="long-billing-relay-address-1288@example.com",
            note="Billing relay used by the support team",
            created_by=staff,
        )
        TierOverride.objects.create(
            user=member,
            original_tier=main,
            override_tier=premium,
            expires_at=timezone.now() + datetime.timedelta(days=14),
            granted_by=staff,
            source="studio",
        )
        connection.close()
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        context, page = _page(browser)
        surfaces = {
            "user": f"{django_server}/studio/users/{member_pk}/",
            "users-sorted": (
                f"{django_server}/studio/users/?sort=last_login"
                "&tag=support-priority&q=member-1288"
            ),
            "crm-filtered": (
                f"{django_server}/studio/crm/?filter=active"
                "&tag=support-priority&q=member-1288"
            ),
            "note-form": (
                f"{django_server}/studio/users/{member_pk}/notes/new"
                f"?next={quote(f'/studio/crm/{crm_pk}/#member-notes', safe='')}"
            ),
        }
        for viewport, size in (
            ("desktop", {"width": 1280, "height": 900}),
            ("mobile", {"width": 393, "height": 852}),
        ):
            page.set_viewport_size(size)
            for theme in ("light", "dark"):
                for surface, url in surfaces.items():
                    page.goto(url, wait_until="domcontentloaded")
                    analytics_off = page.get_by_role("button", name="Keep analytics off")
                    if analytics_off.count() and analytics_off.is_visible():
                        with page.expect_navigation():
                            analytics_off.click()
                    page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
                    page.reload(wait_until="domcontentloaded")
                    assert page.locator("text=Page not found").count() == 0
                    assert page.locator("text=Server Error").count() == 0
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= "
                        "document.documentElement.clientWidth + 2"
                    )
                    page.screenshot(
                        path=SCREENSHOTS / f"{surface}-{viewport}-{theme}.png",
                        full_page=True,
                    )
        context.close()
