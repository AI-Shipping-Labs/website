"""Focused Playwright coverage for account privacy export/deletion (#1210)."""

import copy
import json
import os
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from accounts.services.privacy import SCHEMA_VERSION
from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_staff_user,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

FORBIDDEN_PAYMENT_CARD_FIELDS = frozenset(
    {
        "card_last4",
        "card_number",
        "last4",
        "last_four",
        "payment_method_card",
    }
)


def _iter_mapping_keys(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = (*path, str(key))
            yield key_path
            yield from _iter_mapping_keys(child, key_path)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child, path)


def _assert_export_excludes_secrets(payload, *, plaintext, key_hash):
    rendered = json.dumps(payload)
    assert plaintext not in rendered
    assert key_hash not in rendered
    assert "password" not in rendered.lower()

    payment = payload["membership_payment"]
    assert payment["card_data"] == "not_stored"
    forbidden_paths = [
        ".".join(("membership_payment", *path))
        for path in _iter_mapping_keys(payment)
        if path[-1].lower().replace("-", "_") in FORBIDDEN_PAYMENT_CARD_FIELDS
    ]
    assert not forbidden_paths, f"exported payment card fields: {forbidden_paths}"


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
    plan = Plan.objects.create(member=user, sprint=sprint, title="Privacy Plan")
    collision_timestamp = timezone.now().replace(microsecond=554242)
    Plan.objects.filter(pk=plan.pk).update(
        created_at=collision_timestamp,
        updated_at=collision_timestamp,
    )

    api_key, plaintext = MemberAPIKey.create_for_user(
        user=user,
        name="portable export",
        scopes=["plans:read"],
    )
    return user, api_key, plaintext


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

            assert payload["manifest"]["schema_version"] == SCHEMA_VERSION
            assert payload["account_profile"]["first_name"] == "Portable"
            assert payload["membership_payment"]["effective_tier"]["slug"] == "main"
            assert payload["learning_content"]["course_enrollments"]
            assert payload["events_community"]["event_registrations"]
            assert payload["sprints_plans"]["plans"]
            assert "communications_activity" in payload

            keys = payload["auth_security"]["member_api_keys"]
            assert keys[0]["name"] == "portable export"
            assert keys[0]["lookup_prefix"] == api_key.lookup_prefix
            assert "4242" in payload["sprints_plans"]["plans"][0]["created_at"]
            _assert_export_excludes_secrets(
                payload,
                plaintext=plaintext,
                key_hash=key_hash,
            )

            payment_leak = copy.deepcopy(payload)
            payment_leak["membership_payment"]["last4"] = "4242"
            with pytest.raises(AssertionError, match="membership_payment.last4"):
                _assert_export_excludes_secrets(
                    payment_leak,
                    plaintext=plaintext,
                    key_hash=key_hash,
                )

            plaintext_leak = copy.deepcopy(payload)
            plaintext_leak["auth_security"]["member_api_keys"][0]["key"] = plaintext
            with pytest.raises(AssertionError):
                _assert_export_excludes_secrets(
                    plaintext_leak,
                    plaintext=plaintext,
                    key_hash=key_hash,
                )

            key_hash_leak = copy.deepcopy(payload)
            key_hash_leak["auth_security"]["member_api_keys"][0]["key_hash"] = key_hash
            with pytest.raises(AssertionError):
                _assert_export_excludes_secrets(
                    key_hash_leak,
                    plaintext=plaintext,
                    key_hash=key_hash,
                )

            password_leak = copy.deepcopy(payload)
            password_leak["account_profile"]["password_hash"] = "forbidden"
            with pytest.raises(AssertionError):
                _assert_export_excludes_secrets(
                    password_leak,
                    plaintext=plaintext,
                    key_hash=key_hash,
                )
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
class TestAccountPrivacyDeletionRequest1398:
    def test_free_member_requests_deletion_and_remains_signed_in(
        self, django_server, django_db_blocker, browser
    ):
        email = "privacy-request-free-1398@test.com"
        with django_db_blocker.unblock():
            create_user(email, tier_slug="free")

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

            request_section = page.get_by_test_id("privacy-data-section")
            expect(request_section).to_contain_text("Request account deletion")
            expect(request_section).to_contain_text("does not delete your account immediately")
            expect(request_section).to_contain_text(email)
            request_button = page.get_by_role("button", name="Request account deletion")
            expect(request_button).to_be_enabled()
            request_button.focus()
            page.keyboard.press("Enter")

            page.wait_for_url(f"{django_server}/account/#privacy-data-section")
            received = page.get_by_role("status")
            expect(received).to_contain_text("Deletion request received")
            expect(received).to_contain_text(email)
            expect(received).to_contain_text("no later than one month")
            expect(received.get_by_role("link", name="team@aishippinglabs.com")).to_be_visible()
            expect(received.locator("p", has_text=email)).to_be_visible()

            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_test_id("privacy-request-received")).to_be_visible()
            expect(page.get_by_test_id("privacy-request-submit")).to_have_count(0)
        finally:
            context.close()

    def test_newsletter_paid_and_staff_accounts_get_the_same_request_action(
        self, django_server, django_db_blocker, browser
    ):
        from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER

        with django_db_blocker.unblock():
            newsletter = create_user("privacy-newsletter-request-1398@test.com", tier_slug="free")
            newsletter.signup_source = SIGNUP_SOURCE_NEWSLETTER
            newsletter.account_activated = False
            newsletter.save(update_fields=["signup_source", "account_activated"])
            paid = create_user("privacy-paid-request-1398@test.com", tier_slug="basic")
            paid.subscription_id = "sub_request_1398"
            paid.save(update_fields=["subscription_id"])
            create_staff_user("privacy-staff-request-1398@test.com")

        for email in (
            newsletter.email,
            paid.email,
            "privacy-staff-request-1398@test.com",
        ):
            context = auth_context(browser, email)
            try:
                page = context.new_page()
                page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
                expect(page.get_by_test_id("privacy-request-submit")).to_be_visible()
                page.get_by_test_id("privacy-request-submit").click()
                received = page.get_by_test_id("privacy-request-received")
                expect(received).to_be_visible()
                expect(received.locator("p", has_text=email)).to_be_visible()
            finally:
                context.close()

    def test_delivery_failure_is_truthful_and_retryable(
        self, django_server, django_db_blocker, browser
    ):
        from integrations.config import clear_config_cache
        from integrations.models import IntegrationSetting

        email = "privacy-delivery-retry-1398@test.com"
        with django_db_blocker.unblock():
            create_user(email, tier_slug="free")
            IntegrationSetting.objects.create(
                key="PRIVACY_REQUEST_EMAIL",
                value="invalid-recipient",
            )
            clear_config_cache()

        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            page.get_by_test_id("privacy-request-submit").click()

            expect(page).to_have_url(f"{django_server}/account/api/request-deletion")
            error = page.get_by_test_id("privacy-request-error")
            expect(error).to_contain_text("could not deliver")
            expect(error.get_by_role("link", name="Email team@aishippinglabs.com")).to_have_attribute(
                "href", "mailto:team@aishippinglabs.com"
            )
            expect(page.get_by_test_id("privacy-request-received")).to_have_count(0)
            expect(page.get_by_test_id("privacy-request-submit")).to_be_visible()

            with django_db_blocker.unblock():
                IntegrationSetting.objects.filter(key="PRIVACY_REQUEST_EMAIL").update(
                    value="team@aishippinglabs.com"
                )
                clear_config_cache()
            page.get_by_test_id("privacy-request-submit").click()
            expect(page.get_by_test_id("privacy-request-received")).to_be_visible()
        finally:
            context.close()

    def test_old_self_service_deletion_urls_are_gone(self, django_server, browser):
        email = "privacy-retired-routes-1398@test.com"
        create_user(email, tier_slug="free")
        context = auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            csrf_token = next(
                cookie["value"]
                for cookie in context.cookies()
                if cookie["name"] == "csrftoken"
            )
            post_response = page.request.post(
                f"{django_server}/account/api/delete-account",
                form={"confirm_email": email, "current_password": DEFAULT_PASSWORD},
                headers={"X-CSRFToken": csrf_token},
            )
            get_response = page.request.get(f"{django_server}/account/deleted")
            assert post_response.status == 404
            assert get_response.status == 404

            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_test_id("privacy-request-submit")).to_be_visible()
            expect(page.get_by_test_id("privacy-request-email")).to_have_text(email)
        finally:
            context.close()


def test_privacy_policy_mentions_request_timeline_and_retention(django_server, page):
    page.goto(f"{django_server}/privacy/", wait_until="domcontentloaded")

    body = page.locator("body")
    expect(body).to_contain_text("Privacy & data section")
    expect(body).to_contain_text("does not delete the account immediately")
    expect(body).to_contain_text("no later than one month")
    expect(body).to_contain_text("does not promise unconditional erasure")
    expect(body).to_contain_text("team@aishippinglabs.com")
    expect(body).to_contain_text("local linked copies are included")
    expect(body).to_contain_text("Billing records are kept")
