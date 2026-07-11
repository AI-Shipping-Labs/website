"""Focused Playwright coverage for account privacy export/deletion (#1210)."""

import json
import os
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_staff_user,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _login_attempt(page, django_server, email, password=DEFAULT_PASSWORD):
    page.goto(f"{django_server}/accounts/login/", wait_until="domcontentloaded")
    page.locator("#login-email").fill(email)
    page.locator("#login-password").fill(password)
    page.locator("#login-submit").click()


def _download_export(page, email):
    with page.expect_download() as download_info:
        page.get_by_test_id("privacy-export-link").click()
    download = download_info.value
    assert download.suggested_filename.startswith("ai-shipping-labs-data-")
    assert download.suggested_filename.endswith(".json")
    payload = json.loads(Path(download.path()).read_text())
    assert payload["manifest"]["primary_email"] == email
    return payload


def _seed_member_export_data(email):
    from accounts.models import MemberAPIKey
    from content.models.course import Course, Module, Unit, UserCourseProgress
    from content.models.enrollment import Enrollment
    from events.models import Event, EventRegistration
    from plans.models import Plan, Sprint

    user = create_user(email, tier_slug="main")
    user.first_name = "Portable"
    user.dashboard_dismissals = ["slack_join"]
    user.save(update_fields=["first_name", "dashboard_dismissals"])

    course = Course.objects.create(
        title="Privacy Course",
        slug="privacy-course-1210",
        status="published",
        required_level=0,
    )
    module = Module.objects.create(course=course, title="Module", slug="module")
    unit = Unit.objects.create(module=module, title="Unit", slug="unit")
    Enrollment.objects.create(user=user, course=course)
    UserCourseProgress.objects.create(
        user=user,
        unit=unit,
        completed_at=timezone.now(),
    )

    event = Event.objects.create(
        title="Privacy Event",
        slug="privacy-event-1210",
        status="upcoming",
        start_datetime=timezone.now() + timezone.timedelta(days=2),
    )
    EventRegistration.objects.create(user=user, event=event)

    sprint = Sprint.objects.create(
        name="Privacy Sprint",
        slug="privacy-sprint-1210",
        start_date=timezone.localdate(),
        min_tier_level=0,
        status="active",
    )
    Plan.objects.create(member=user, sprint=sprint, title="Privacy Plan")

    api_key, plaintext = MemberAPIKey.create_for_user(
        user=user,
        name="portable export",
        scopes=["plans:read"],
    )
    return user, api_key, plaintext


def _user_exists(email):
    from django.db import connection

    from accounts.models import User

    exists = User.objects.filter(email=email).exists()
    connection.close()
    return exists


def _privacy_log_reasons(email=None):
    from django.db import connection

    from accounts.models import PrivacyRequestLog, User

    qs = PrivacyRequestLog.objects.filter(request_type=PrivacyRequestLog.REQUEST_DELETE)
    if email:
        user = User.objects.filter(email=email).first()
        if user:
            qs = qs.filter(old_user_id=user.pk)
    reasons = list(qs.order_by("requested_at").values_list("blocker_reason", flat=True))
    connection.close()
    return reasons


@pytest.mark.django_db(transaction=True)
class TestAccountPrivacyExport1210:
    def test_main_member_downloads_portable_json_without_secrets(
        self, django_server, django_db_blocker, browser
    ):
        email = "privacy-main-1210@test.com"
        with django_db_blocker.unblock():
            _, api_key, plaintext = _seed_member_export_data(email)
            key_hash = api_key.key_hash

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            expect(page.get_by_test_id("privacy-data-section")).to_be_visible()
            payload = _download_export(page, email)

            assert payload["manifest"]["schema_version"] == "2026-07-11.1"
            assert payload["account_profile"]["first_name"] == "Portable"
            assert payload["membership_payment"]["effective_tier"]["slug"] == "main"
            assert payload["learning_content"]["course_enrollments"]
            assert payload["events_community"]["event_registrations"]
            assert payload["sprints_plans"]["plans"]
            assert "communications_activity" in payload

            keys = payload["auth_security"]["member_api_keys"]
            assert keys[0]["name"] == "portable export"
            assert keys[0]["lookup_prefix"] == api_key.lookup_prefix
            rendered = json.dumps(payload)
            assert plaintext not in rendered
            assert key_hash not in rendered
            assert "password" not in rendered.lower()
            assert payload["membership_payment"]["card_data"] == "not_stored"
            assert "4242" not in rendered
        finally:
            context.close()

    def test_newsletter_only_subscriber_can_export_empty_member_categories(
        self, django_server, django_db_blocker, browser
    ):
        from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER

        email = "privacy-newsletter-1210@test.com"
        with django_db_blocker.unblock():
            user = create_user(email, tier_slug="free", unsubscribed=False)
            user.signup_source = SIGNUP_SOURCE_NEWSLETTER
            user.account_activated = False
            user.save(update_fields=["signup_source", "account_activated"])

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            expect(page.get_by_test_id("privacy-data-section")).to_be_visible()
            payload = _download_export(page, email)

            assert payload["account_profile"]["unsubscribed"] is False
            assert payload["learning_content"]["course_enrollments"] == []
            assert payload["events_community"]["event_registrations"] == []
            assert payload["sprints_plans"]["plans"] == []
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestAccountPrivacyDeletion1210:
    def test_paid_member_is_blocked_before_subscription_cleanup(
        self, django_server, django_db_blocker, browser
    ):
        email = "privacy-paid-1210@test.com"
        with django_db_blocker.unblock():
            user = create_user(email, tier_slug="basic")
            user.subscription_id = "sub_active_1210"
            user.save(update_fields=["subscription_id"])

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            expect(page.get_by_test_id("privacy-active-subscription-note")).to_be_visible()
            page.get_by_test_id("privacy-delete-confirmation").click()
            page.get_by_test_id("privacy-confirm-email").fill(email)
            page.get_by_test_id("privacy-current-password").fill(DEFAULT_PASSWORD)
            page.get_by_test_id("privacy-delete-submit").click()

            expect(page.get_by_test_id("privacy-delete-error")).to_contain_text(
                "active subscription"
            )
        finally:
            context.close()

        with django_db_blocker.unblock():
            assert _user_exists(email)
            assert "active_subscription" in _privacy_log_reasons(email)

    def test_free_member_deletes_account_and_old_password_no_longer_signs_in(
        self, django_server, django_db_blocker, browser
    ):
        email = "privacy-free-delete-1210@test.com"
        with django_db_blocker.unblock():
            _seed_member_export_data(email)

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            page.get_by_test_id("privacy-delete-confirmation").click()
            page.get_by_test_id("privacy-confirm-email").fill(email)
            page.get_by_test_id("privacy-current-password").fill(DEFAULT_PASSWORD)
            page.get_by_test_id("privacy-delete-submit").click()
            page.wait_for_url(f"{django_server}/account/deleted")
            expect(
                page.get_by_test_id("account-deleted-confirmation")
            ).to_contain_text("Your local AI Shipping Labs account has been deleted")
        finally:
            context.close()

        with django_db_blocker.unblock():
            assert not _user_exists(email)

        page = browser.new_page()
        try:
            _login_attempt(page, django_server, email)
            expect(page.locator("#login-error")).to_be_visible()
            expect(page.locator("#login-error")).to_contain_text("Invalid")
        finally:
            page.close()

    def test_typo_or_wrong_password_keeps_account_and_audits_attempts(
        self, django_server, django_db_blocker, browser
    ):
        email = "privacy-guard-1210@test.com"
        with django_db_blocker.unblock():
            create_user(email, tier_slug="free")

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            page.get_by_test_id("privacy-delete-confirmation").click()
            page.get_by_test_id("privacy-confirm-email").fill("typo-" + email)
            page.get_by_test_id("privacy-current-password").fill(DEFAULT_PASSWORD)
            page.get_by_test_id("privacy-delete-submit").click()
            expect(page.get_by_test_id("privacy-delete-error")).to_contain_text(
                "could not confirm"
            )

            page.get_by_test_id("privacy-delete-confirmation").click()
            page.get_by_test_id("privacy-confirm-email").fill(email)
            page.get_by_test_id("privacy-current-password").fill("wrong-password")
            page.get_by_test_id("privacy-delete-submit").click()
            expect(page.get_by_test_id("privacy-delete-error")).to_contain_text(
                "could not confirm"
            )
        finally:
            context.close()

        with django_db_blocker.unblock():
            assert _user_exists(email)
            reasons = _privacy_log_reasons(email)
            assert "bad_confirmation" in reasons
            assert "bad_password" in reasons

    def test_staff_export_available_but_self_delete_blocked(self, django_server, browser):
        email = "privacy-staff-1210@test.com"
        create_staff_user(email)

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            expect(page.get_by_test_id("privacy-export-link")).to_be_visible()
            expect(page.get_by_test_id("privacy-staff-block")).to_contain_text(
                "cannot be deleted"
            )

            response = page.request.post(
                f"{django_server}/account/api/delete-account",
                form={
                    "confirm_email": email,
                    "current_password": DEFAULT_PASSWORD,
                },
            )
            assert response.status == 403
        finally:
            context.close()

        assert _user_exists(email)


def test_privacy_policy_mentions_self_service_and_retention(django_server, page):
    page.goto(f"{django_server}/privacy/", wait_until="domcontentloaded")

    body = page.locator("body")
    expect(body).to_contain_text("Privacy & data section")
    expect(body).to_contain_text("local account deletion")
    expect(body).to_contain_text("local linked copies are included")
    expect(body).to_contain_text("Billing records are kept")
