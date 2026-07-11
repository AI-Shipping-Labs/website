"""Playwright coverage for member login email changes (#1209)."""

import os
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _login_with_password(page, base_url, email, password=DEFAULT_PASSWORD):
    page.goto(f"{base_url}/accounts/login/", wait_until="domcontentloaded")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")


def _seed_member(email, *, tier_slug="basic"):
    from django.db import connection

    user = create_user(email, tier_slug=tier_slug, password=DEFAULT_PASSWORD)
    user.account_activated = True
    user.email_verified = True
    user.save(update_fields=["account_activated", "email_verified"])
    connection.close()
    return user


@pytest.mark.django_db(transaction=True)
class TestMemberRequestsEmailChange:
    @pytest.mark.core
    def test_member_requests_change_without_losing_old_login(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            _seed_member("old-member-1209@test.com")

        context = auth_context(browser, "old-member-1209@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            expect(page.locator("#login-email-section")).to_be_visible()
            expect(page.locator("#current-login-email")).to_have_text(
                "old-member-1209@test.com"
            )
            page.fill("#change-email-new-email", "new-member-1209@test.com")
            page.fill("#change-email-current-password", DEFAULT_PASSWORD)
            page.click("#change-email-submit")

            expect(page.locator("#change-email-success")).to_contain_text(
                "Verification link sent to new-member-1209@test.com"
            )
            expect(page.locator("#current-login-email")).to_have_text(
                "old-member-1209@test.com"
            )
            expect(page.locator("[data-testid='pending-login-email']")).to_contain_text(
                "new-member-1209@test.com"
            )
        finally:
            context.close()

        old_login = browser.new_context()
        try:
            page = old_login.new_page()
            _login_with_password(
                page,
                django_server,
                "old-member-1209@test.com",
            )
            page.wait_for_url(f"{django_server}/")
        finally:
            old_login.close()

        new_login = browser.new_context()
        try:
            page = new_login.new_page()
            _login_with_password(
                page,
                django_server,
                "new-member-1209@test.com",
            )
            expect(page.locator("#login-error")).to_be_visible()
        finally:
            new_login.close()

    def test_wrong_password_shows_error_and_sends_no_email(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            _seed_member("wrong-password-1209@test.com")

        context = auth_context(browser, "wrong-password-1209@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            page.fill("#change-email-new-email", "new-wrong-1209@test.com")
            page.fill("#change-email-current-password", "WrongPassword123!")
            page.click("#change-email-submit")

            expect(page.locator("#change-email-error")).to_be_visible()
            expect(page.locator("#change-email-success")).to_be_hidden()
        finally:
            context.close()

        with django_db_blocker.unblock():
            from accounts.models import EmailChangeRequest, User
            from email_app.models import EmailLog

            user = User.objects.get(email="wrong-password-1209@test.com")
            assert user.email == "wrong-password-1209@test.com"
            assert not EmailChangeRequest.objects.filter(user=user).exists()
            assert not EmailLog.objects.filter(
                email_type="account_email_change_confirm",
                user=user,
            ).exists()

    def test_member_cannot_request_other_primary_or_alias(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            _seed_member("collision-1209@test.com")
            _seed_member("taken-1209@test.com")
            alias_owner = _seed_member("alias-owner-1209@test.com")
            from django.db import connection

            from accounts.models import EmailAlias

            EmailAlias.objects.create(
                user=alias_owner,
                email="relay-1209@test.com",
            )
            connection.close()

        context = auth_context(browser, "collision-1209@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            for email in ["taken-1209@test.com", "relay-1209@test.com"]:
                page.fill("#change-email-new-email", email)
                page.fill("#change-email-current-password", DEFAULT_PASSWORD)
                page.click("#change-email-submit")
                expect(page.locator("#change-email-error")).to_have_text(
                    "That email cannot be used for this account."
                )
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberConfirmsEmailChange:
    @pytest.mark.core
    def test_member_confirms_new_email_and_sees_account_updated(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            user = _seed_member("confirm-old-1209@test.com")
            from django.db import connection

            from accounts.services.email_change import request_email_change

            _request_obj, token = request_email_change(
                user,
                "confirm-new-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            connection.close()

        page = browser.new_page()
        page.goto(
            f"{django_server}/account/change-email/confirm?token={token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result']")).to_be_visible()
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "account email was changed successfully"
        )

        _login_with_password(page, django_server, "confirm-new-1209@test.com")
        page.wait_for_url(f"{django_server}/")

        context = auth_context(browser, "confirm-new-1209@test.com")
        try:
            account_page = context.new_page()
            account_page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            expect(account_page.locator("#current-login-email")).to_have_text(
                "confirm-new-1209@test.com"
            )
        finally:
            context.close()

        with django_db_blocker.unblock():
            from accounts.models import EmailAlias, User
            from email_app.models import EmailLog

            changed = User.objects.get(email="confirm-new-1209@test.com")
            assert EmailAlias.objects.filter(
                user=changed,
                email="confirm-old-1209@test.com",
                source=EmailAlias.SOURCE_ACCOUNT_CHANGE,
            ).exists()
            assert EmailLog.objects.filter(
                user=changed,
                recipient_email="confirm-old-1209@test.com",
                email_type="account_email_changed_notice",
            ).exists()

    def test_expired_and_superseded_links_show_recovery_path(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            expired_user = _seed_member("expired-link-1209@test.com")
            superseded_user = _seed_member("superseded-link-1209@test.com")
            from django.core.cache import cache
            from django.db import connection

            from accounts.models import EmailChangeRequest
            from accounts.services.email_change import request_email_change

            expired_request, expired_token = request_email_change(
                expired_user,
                "expired-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            EmailChangeRequest.objects.filter(pk=expired_request.pk).update(
                expires_at=timezone.now() - timedelta(minutes=5)
            )
            cache.clear()
            first, first_token = request_email_change(
                superseded_user,
                "first-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            cache.clear()
            request_email_change(
                superseded_user,
                "second-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            first.refresh_from_db()
            assert first.invalidated_at is not None
            connection.close()

        page = browser.new_page()
        page.goto(
            f"{django_server}/account/change-email/confirm?token={expired_token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "link expired"
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_be_visible()

        page.goto(
            f"{django_server}/account/change-email/confirm?token={first_token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "no longer valid"
        )

        with django_db_blocker.unblock():
            from accounts.models import User

            assert User.objects.get(email="expired-link-1209@test.com")
            assert User.objects.get(email="superseded-link-1209@test.com")
            assert not User.objects.filter(email="first-1209@test.com").exists()

    def test_same_user_alias_promotion_preserves_billing_fields(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            user = _seed_member("alias-primary-1209@test.com")
            user.stripe_customer_id = "cus_alias_1209"
            user.subscription_id = "sub_alias_1209"
            user.save(update_fields=["stripe_customer_id", "subscription_id"])
            from django.db import connection

            from accounts.models import EmailAlias
            from accounts.services.email_change import request_email_change

            EmailAlias.objects.create(
                user=user,
                email="billing-alias-1209@test.com",
            )
            _request_obj, token = request_email_change(
                user,
                "billing-alias-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            connection.close()

        page = browser.new_page()
        page.goto(
            f"{django_server}/account/change-email/confirm?token={token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "account email was changed successfully"
        )

        with django_db_blocker.unblock():
            from accounts.models import EmailAlias, User

            changed = User.objects.get(email="billing-alias-1209@test.com")
            assert changed.stripe_customer_id == "cus_alias_1209"
            assert changed.subscription_id == "sub_alias_1209"
            assert not EmailAlias.objects.filter(
                email="billing-alias-1209@test.com"
            ).exists()
            assert EmailAlias.objects.filter(
                user=changed,
                email="alias-primary-1209@test.com",
            ).exists()

    def test_slack_state_is_marked_stale_without_disconnect_ui(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            user = _seed_member("slack-change-1209@test.com", tier_slug="main")
            user.slack_member = True
            user.slack_user_id = "U1209"
            user.slack_checked_at = timezone.now()
            user.save(
                update_fields=[
                    "slack_member",
                    "slack_user_id",
                    "slack_checked_at",
                ]
            )
            from django.db import connection

            from accounts.services.email_change import request_email_change

            _request_obj, token = request_email_change(
                user,
                "slack-new-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            connection.close()

        page = browser.new_page()
        page.goto(
            f"{django_server}/account/change-email/confirm?token={token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "account email was changed successfully"
        )

        context = auth_context(browser, "slack-new-1209@test.com")
        try:
            account_page = context.new_page()
            account_page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            expect(
                account_page.locator("[data-testid='slack-account-card']")
            ).to_have_count(0)
        finally:
            context.close()

        with django_db_blocker.unblock():
            from accounts.models import User

            changed = User.objects.get(email="slack-new-1209@test.com")
            assert changed.slack_member is True
            assert changed.slack_user_id == "U1209"
            assert changed.slack_checked_at is None


@pytest.mark.django_db(transaction=True)
class TestNewsletterOnlyEmailChangeGating:
    def test_newsletter_only_account_keeps_trimmed_page(self, django_server, django_db_blocker, browser):
        with django_db_blocker.unblock():
            from django.db import connection

            from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER

            user = create_user(
                "newsletter-only-1209@test.com",
                tier_slug="free",
                password=DEFAULT_PASSWORD,
            )
            user.signup_source = SIGNUP_SOURCE_NEWSLETTER
            user.account_activated = False
            user.email_verified = True
            user.save(
                update_fields=[
                    "signup_source",
                    "account_activated",
                    "email_verified",
                ]
            )
            connection.close()

        context = auth_context(browser, "newsletter-only-1209@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            expect(page.locator("#newsletter-only-cta")).to_be_visible()
            expect(page.locator("#email-preferences-section")).to_be_visible()
            expect(page.locator("#login-email-section")).to_have_count(0)
            expect(page.locator("#change-email-form")).to_have_count(0)
        finally:
            context.close()
