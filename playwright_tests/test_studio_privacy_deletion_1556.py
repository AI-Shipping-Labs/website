"""Studio account-deletion request journeys for issue #1556."""

import os
import uuid
from pathlib import Path

import pytest
from django.db import connection
from playwright.sync_api import expect

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog, User
from accounts.services.privacy import (
    normalized_privacy_email_hash,
    request_account_deletion,
)
from accounts.services.privacy_recipient import encrypt_recipient
from email_app.services.email_service import EmailServiceError
from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey
from tests.fixtures import set_membership

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "issue-1556-screenshots"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _dismiss_analytics_prompt(page):
    button = page.get_by_role("button", name="Keep analytics off")
    if button.count() and button.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            button.click()


def _accepted_request(user):
    request_log = PrivacyRequestLog.objects.create(
        request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
        status=PrivacyRequestLog.STATUS_REQUESTED,
        old_user_id=user.pk,
        normalized_email_hash=normalized_privacy_email_hash(user.email),
        email_domain=user.email.rsplit("@", 1)[-1],
    )
    connection.close()
    return request_log


def _review_page(browser, django_server, operator_email, request_id):
    context = auth_context(browser, operator_email)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/privacy/deletion-requests/{request_id}/",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    return context, page


def _confirm(page, email):
    page.get_by_test_id("privacy-confirm-email").fill(email)
    page.get_by_test_id("privacy-confirm-acknowledge").check()
    page.get_by_test_id("privacy-delete-account").click()
    expect(page.get_by_test_id("privacy-deletion-header")).to_be_visible()


@browser_journey
def test_superuser_follows_handoff_and_deletes_eligible_account(
    django_server,
    browser,
):
    """The request anchor leads through confirmation to a durable sent result."""
    admin = create_staff_user("privacy-e2e-admin@test.com")
    member = create_user("privacy-e2e-member@test.com")
    request_result = request_account_deletion(member)
    request_id = request_result.audit_log_id
    member_id = member.pk
    connection.close()

    context = auth_context(browser, admin.email)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/users/{member_id}/#privacy-deletion-request",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    expect(page.get_by_test_id("privacy-deletion-request-callout")).to_be_visible()
    page.get_by_test_id("privacy-deletion-review").click()
    _confirm(page, member.email)
    expect(page.get_by_test_id("privacy-deletion-completed")).to_contain_text(
        "Account deleted",
    )
    expect(page.get_by_test_id("privacy-confirmation-sent")).to_be_visible()
    _shot(page, "01-account-deleted-confirmation-sent")

    delivery = PrivacyCompletionDelivery.objects.get(request_id=request_id)
    assert delivery.email_log.recipient_email == "[deleted-account]"
    assert delivery.recipient_ciphertext == ""
    assert delivery.status == PrivacyCompletionDelivery.STATUS_SENT
    assert not User.objects.filter(pk=member_id).exists()
    connection.close()
    context.close()


@browser_journey
def test_staff_reviewer_can_inspect_but_cannot_execute(django_server, browser):
    """Non-superuser staff sees facts and the explicit authorization boundary."""
    staff = create_staff_user("privacy-reviewer@test.com")
    staff.is_superuser = False
    staff.save(update_fields=["is_superuser"])
    member = create_user("privacy-staff-review-member@test.com")
    request_log = _accepted_request(member)

    context, page = _review_page(browser, django_server, staff.email, request_log.pk)
    expect(page.get_by_test_id("privacy-deletion-facts")).to_be_visible()
    expect(page.get_by_test_id("privacy-superuser-required")).to_be_visible()
    expect(page.get_by_test_id("privacy-delete-account")).to_have_count(0)
    _shot(page, "02-staff-read-only-review")
    assert User.objects.filter(pk=member.pk).exists()
    connection.close()
    context.close()


@browser_journey
def test_active_subscription_is_a_truthful_retryable_blocker(django_server, browser):
    """Stripe-backed members remain intact and create no completion delivery."""
    admin = create_staff_user("privacy-sub-admin@test.com")
    member = create_user("privacy-sub-member@test.com")
    set_membership(member, subscription_id='sub_privacy_e2e')
    request_log = _accepted_request(member)

    context, page = _review_page(browser, django_server, admin.email, request_log.pk)
    expect(page.get_by_test_id("privacy-deletion-blocker")).to_contain_text(
        "Active subscription cleanup is required",
    )
    _confirm(page, member.email)
    expect(page.get_by_test_id("privacy-deletion-blocker")).to_be_visible()
    _shot(page, "03-active-subscription-blocker")
    assert User.objects.filter(pk=member.pk).exists()
    assert request_log.execution_audits.filter(blocker_reason="active_subscription").exists()
    assert not PrivacyCompletionDelivery.objects.filter(request=request_log).exists()
    connection.close()
    context.close()


@browser_journey
def test_protected_staff_account_cannot_be_deleted(django_server, browser):
    """A separate superuser cannot override the canonical staff-account guard."""
    admin = create_staff_user("privacy-protector@test.com")
    target = create_staff_user("privacy-protected-target@test.com")
    request_log = _accepted_request(target)

    context, page = _review_page(browser, django_server, admin.email, request_log.pk)
    expect(page.get_by_test_id("privacy-deletion-blocker")).to_contain_text(
        "Protected staff",
    )
    _confirm(page, target.email)
    _shot(page, "04-protected-staff-blocker")
    assert User.objects.filter(pk=target.pk, is_staff=True).exists()
    assert request_log.execution_audits.filter(blocker_reason="staff_account").exists()
    assert not PrivacyCompletionDelivery.objects.filter(request=request_log).exists()
    connection.close()
    context.close()


@browser_journey
def test_stale_request_cannot_be_retargeted(django_server, browser):
    """Changed login identity blocks old, new, and forged confirmation values."""
    admin = create_staff_user("privacy-stale-admin@test.com")
    member = create_user("privacy-old-login@test.com")
    request_log = _accepted_request(member)
    member.email = "privacy-new-login@test.com"
    member.save(update_fields=["email"])
    forged = create_user("privacy-forged-target@test.com")
    connection.close()

    context, page = _review_page(browser, django_server, admin.email, request_log.pk)
    expect(page.get_by_test_id("privacy-deletion-blocker")).to_contain_text(
        "fresh deletion request",
    )
    _confirm(page, forged.email)
    _shot(page, "05-stale-identity-blocker")
    assert User.objects.filter(pk=member.pk).exists()
    assert User.objects.filter(pk=forged.pk).exists()
    assert request_log.execution_audits.filter(blocker_reason="identity_changed").exists()
    connection.close()
    context.close()


@browser_journey
def test_two_tabs_converge_on_one_irreversible_action(django_server, browser):
    """A stale second form reaches the terminal result without duplicate work."""
    admin = create_staff_user("privacy-tabs-admin@test.com")
    member = create_user("privacy-tabs-member@test.com")
    request_log = _accepted_request(member)

    context = auth_context(browser, admin.email)
    first = context.new_page()
    second = context.new_page()
    url = f"{django_server}/studio/privacy/deletion-requests/{request_log.pk}/"
    first.goto(url, wait_until="domcontentloaded")
    _dismiss_analytics_prompt(first)
    second.goto(url, wait_until="domcontentloaded")
    first.get_by_test_id("privacy-confirm-email").fill(member.email)
    first.get_by_test_id("privacy-confirm-acknowledge").check()
    second.get_by_test_id("privacy-confirm-email").fill(member.email)
    second.get_by_test_id("privacy-confirm-acknowledge").check()
    first.get_by_test_id("privacy-delete-account").click()
    second.get_by_test_id("privacy-delete-account").click()
    expect(second.get_by_test_id("privacy-deletion-completed")).to_be_visible()
    _shot(second, "06-two-tab-terminal-result")

    assert request_log.execution_audits.filter(status="completed").count() == 1
    assert PrivacyCompletionDelivery.objects.filter(request=request_log).count() == 1
    connection.close()
    context.close()


@browser_journey
def test_known_confirmation_failure_is_visible_and_recoverable(
    django_server,
    browser,
    monkeypatch,
):
    """Deletion stays complete while a known send failure gets one safe retry."""
    admin = create_staff_user("privacy-retry-admin@test.com")
    member = create_user("privacy-retry-member@test.com")
    request_log = _accepted_request(member)
    original_send = __import__(
        "email_app.services.email_service",
        fromlist=["EmailService"],
    ).EmailService.send_prepared

    def fail_known(_service, _prepared):
        raise EmailServiceError("known test transport failure")

    monkeypatch.setattr(
        "email_app.services.email_service.EmailService.send_prepared",
        fail_known,
    )
    context, page = _review_page(browser, django_server, admin.email, request_log.pk)
    _confirm(page, member.email)
    expect(page.get_by_test_id("privacy-confirmation-failed")).to_be_visible()
    expect(page.get_by_test_id("privacy-confirmation-retry")).to_be_visible()
    _shot(page, "07-known-delivery-failure")
    assert not User.objects.filter(pk=member.pk).exists()

    monkeypatch.setattr(
        "email_app.services.email_service.EmailService.send_prepared",
        original_send,
    )
    page.get_by_test_id("privacy-confirmation-retry").click()
    expect(page.get_by_test_id("privacy-confirmation-sent")).to_be_visible()
    expect(page.get_by_test_id("privacy-confirmation-retry")).to_have_count(0)
    _shot(page, "07-known-delivery-recovered")
    delivery = PrivacyCompletionDelivery.objects.get(request=request_log)
    assert delivery.attempt_count == 2
    connection.close()
    context.close()


@browser_journey
def test_unknown_confirmation_outcome_disables_resend(django_server, browser):
    """A stranded transport claim shows evidence guidance without an address."""
    admin = create_staff_user("privacy-unknown-admin@test.com")
    member = create_user("privacy-unknown-member@test.com")
    request_log = _accepted_request(member)
    request_log.status = PrivacyRequestLog.STATUS_COMPLETED
    request_log.save(update_fields=["status"])
    PrivacyRequestLog.objects.create(
        request_type=PrivacyRequestLog.REQUEST_DELETE,
        status=PrivacyRequestLog.STATUS_COMPLETED,
        old_user_id=member.pk,
        normalized_email_hash=request_log.normalized_email_hash,
        email_domain=request_log.email_domain,
        originating_request=request_log,
        operator_user_id=admin.pk,
    )
    PrivacyCompletionDelivery.objects.create(
        request=request_log,
        status=PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        recipient_ciphertext=encrypt_recipient(member.email),
        normalized_email_hash=request_log.normalized_email_hash,
        email_domain=request_log.email_domain,
        dedupe_key=f"account-deletion-completed:{request_log.pk}",
        attempt_count=1,
        claim_token=uuid.uuid4(),
    )
    connection.close()

    context, page = _review_page(browser, django_server, admin.email, request_log.pk)
    unknown = page.get_by_test_id("privacy-confirmation-unknown")
    expect(unknown).to_contain_text("Review provider delivery evidence")
    expect(page.get_by_test_id("privacy-confirmation-retry")).to_have_count(0)
    _shot(page, "08-unknown-outcome-fenced")
    assert member.email not in page.content()
    connection.close()
    context.close()
