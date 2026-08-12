"""Operator paging journeys for the growing Studio lists in issue #1328."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _staff_page(browser, email):
    _create_staff_user(email)
    context = _auth_context(browser, email)
    return context, context.new_page()


def _seed_emissions(count, *, prefix):
    from triggers.models import EventEmission

    emissions = EventEmission.objects.bulk_create([
        EventEmission(
            event_name=f"{prefix}.{index:03d}",
            properties={},
            envelope_id=f"evt_{prefix}_{index:03d}",
        )
        for index in range(count)
    ])
    return emissions


@pytest.mark.django_db(transaction=True)
def test_operator_reaches_older_redirect_and_can_open_it(django_server, browser):
    from integrations.models import Redirect

    Redirect.objects.all().delete()
    Redirect.objects.bulk_create([
        Redirect(
            source_path=f"/pagination-old-{index:02d}",
            target_path=f"/pagination-new-{index:02d}",
        )
        for index in range(26)
    ])
    connection.close()

    context, page = _staff_page(browser, "redirect-pagination@test.com")
    try:
        page.goto(f"{django_server}/studio/redirects/", wait_until="domcontentloaded")
        expect(page.locator("tbody tr")).to_have_count(25)
        expect(page.locator("tbody tr").first).to_contain_text(
            "/pagination-old-00"
        )

        page.get_by_test_id("redirect-list-pager-next").click()
        page.wait_for_url("**/studio/redirects/?page=2")
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("tbody tr").first).to_contain_text(
            "/pagination-old-25"
        )

        page.locator("tbody tr").first.get_by_role("link", name="Edit").click()
        page.wait_for_url("**/studio/redirects/*/edit")
        expect(page.locator('input[name="source_path"]')).to_have_value(
            "/pagination-old-25"
        )
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_operator_pages_active_and_archived_utm_scopes(django_server, browser):
    from integrations.models import UtmCampaign

    UtmCampaign.objects.all().delete()
    for index in range(26):
        UtmCampaign.objects.create(
            name=f"Pagination active {index:02d}",
            slug=f"pagination_active_{index:02d}",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        UtmCampaign.objects.create(
            name=f"Pagination archived {index:02d}",
            slug=f"pagination_archived_{index:02d}",
            default_utm_source="newsletter",
            default_utm_medium="email",
            is_archived=True,
        )
    connection.close()

    context, page = _staff_page(browser, "utm-pagination@test.com")
    try:
        page.goto(
            f"{django_server}/studio/utm-campaigns/?archived=1",
            wait_until="domcontentloaded",
        )
        expect(page.locator("tbody tr")).to_have_count(25)
        page.get_by_test_id("utm-campaign-list-pager-next").click()
        page.wait_for_url("**/studio/utm-campaigns/?archived=1&page=2")
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.get_by_text("Showing archived", exact=True)).to_be_visible()
        expect(page.locator("tbody")).to_contain_text("Pagination archived 00")
        expect(page.locator("tbody")).not_to_contain_text("Pagination active")

        page.goto(
            f"{django_server}/studio/utm-campaigns/",
            wait_until="domcontentloaded",
        )
        page.get_by_test_id("utm-campaign-list-pager-next").click()
        page.wait_for_url("**/studio/utm-campaigns/?page=2")
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("tbody")).to_contain_text("Pagination active 00")
        expect(page.locator("tbody")).not_to_contain_text("Pagination archived")
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_operator_keeps_marketing_page_search_and_status_while_paging(
    django_server,
    browser,
):
    from content.models import MarketingPage

    MarketingPage.objects.all().delete()
    MarketingPage.objects.bulk_create([
        MarketingPage(
            title=f"Pagination launch {index:02d}",
            public_path=f"/pagination-launch-{index:02d}",
            content_markdown="Body",
            status="draft",
        )
        for index in range(26)
    ])
    MarketingPage.objects.create(
        title="Unrelated pagination draft",
        public_path="/unrelated-pagination-draft",
        content_markdown="Body",
        status="draft",
    )
    MarketingPage.objects.create(
        title="Pagination launch published",
        public_path="/pagination-launch-published",
        content_markdown="Body",
        status="published",
    )
    connection.close()

    context, page = _staff_page(browser, "marketing-pagination@test.com")
    try:
        page.goto(
            f"{django_server}/studio/marketing-pages/"
            "?q=Pagination+launch&status=draft",
            wait_until="domcontentloaded",
        )
        expect(page.locator("tbody tr")).to_have_count(25)
        page.get_by_test_id("marketing-page-list-pager-next").click()
        page.wait_for_url("**/studio/marketing-pages/?q=Pagination+launch&status=draft&page=2")

        expect(page.locator('input[name="q"]')).to_have_value("Pagination launch")
        expect(page.locator('select[name="status"]')).to_have_value("draft")
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("tbody")).to_contain_text("Pagination launch 25")
        expect(page.locator("tbody")).not_to_contain_text("Unrelated pagination")
        expect(page.locator("tbody")).not_to_contain_text("published")
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_operator_reaches_emission_beyond_old_cap_and_recovers_from_bad_page(
    django_server,
    browser,
):
    from triggers.models import EventEmission

    EventEmission.objects.all().delete()
    emissions = _seed_emissions(201, prefix="pagination-emission")
    oldest_envelope = emissions[0].envelope_id
    connection.close()

    context, page = _staff_page(browser, "emission-pagination@test.com")
    try:
        page.goto(
            f"{django_server}/studio/triggers/emissions/",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("emission-row")).to_have_count(25)
        page.get_by_test_id("trigger-emission-list-pager-last").click()
        page.wait_for_url("**/studio/triggers/emissions/?page=9")
        expect(page.get_by_test_id("emission-row")).to_have_count(1)
        expect(page.get_by_test_id("emissions-list")).to_contain_text(oldest_envelope)

        page.goto(
            f"{django_server}/studio/triggers/emissions/?page=malformed",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("trigger-emission-list-pager-status")).to_have_text(
            "page 1 of 9"
        )
        page.goto(
            f"{django_server}/studio/triggers/emissions/?page=-4",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("trigger-emission-list-pager-status")).to_have_text(
            "page 1 of 9"
        )
        page.goto(
            f"{django_server}/studio/triggers/emissions/?page=999",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("trigger-emission-list-pager-status")).to_have_text(
            "page 9 of 9"
        )
        page.get_by_test_id("trigger-emission-list-pager-prev").click()
        page.wait_for_url("**/studio/triggers/emissions/?page=8")
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_operator_pages_filtered_delivery_jobs_and_attempts_independently(
    django_server,
    browser,
):
    from triggers.models import (
        EventEmission,
        TriggerSubscription,
        WebhookDelivery,
        WebhookDeliveryJob,
    )

    WebhookDelivery.objects.all().delete()
    WebhookDeliveryJob.objects.all().delete()
    EventEmission.objects.all().delete()
    TriggerSubscription.objects.all().delete()
    subscription = TriggerSubscription.objects.create(
        event_type="custom",
        property_filter={},
        target_url="https://handler.example.com/pagination-primary",
        secret="primary-secret",
    )
    other_subscription = TriggerSubscription.objects.create(
        event_type="custom",
        property_filter={},
        target_url="https://handler.example.com/pagination-other",
        secret="other-secret",
    )
    emissions = _seed_emissions(28, prefix="pagination-delivery")
    failed_jobs = WebhookDeliveryJob.objects.bulk_create([
        WebhookDeliveryJob(
            emission=emission,
            subscription=subscription,
            target_url=subscription.target_url,
            encrypted_secret="encrypted-placeholder",
            secret_version=1,
            request_body="{}",
            status=WebhookDeliveryJob.STATUS_FAILED,
        )
        for emission in emissions[:26]
    ])
    WebhookDelivery.objects.bulk_create([
        WebhookDelivery(
            emission=job.emission,
            subscription=subscription,
            job=job,
            target_url=subscription.target_url,
            attempt=1,
            succeeded=False,
            response_status=500,
        )
        for job in failed_jobs
    ])
    successful_job = WebhookDeliveryJob.objects.create(
        emission=emissions[26],
        subscription=subscription,
        target_url=subscription.target_url,
        encrypted_secret="encrypted-placeholder",
        secret_version=1,
        request_body="{}",
        status=WebhookDeliveryJob.STATUS_SUCCEEDED,
    )
    WebhookDelivery.objects.create(
        emission=emissions[26],
        subscription=subscription,
        job=successful_job,
        target_url=subscription.target_url,
        attempt=1,
        succeeded=True,
        response_status=200,
    )
    other_job = WebhookDeliveryJob.objects.create(
        emission=emissions[27],
        subscription=other_subscription,
        target_url=other_subscription.target_url,
        encrypted_secret="encrypted-placeholder",
        secret_version=1,
        request_body="{}",
        status=WebhookDeliveryJob.STATUS_FAILED,
    )
    WebhookDelivery.objects.create(
        emission=emissions[27],
        subscription=other_subscription,
        job=other_job,
        target_url=other_subscription.target_url,
        attempt=1,
        succeeded=False,
        response_status=500,
    )
    connection.close()

    context, page = _staff_page(browser, "delivery-pagination@test.com")
    try:
        page.goto(
            f"{django_server}/studio/triggers/deliveries/"
            f"?subscription={subscription.pk}&succeeded=false",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("delivery-job-row")).to_have_count(25)
        expect(page.get_by_test_id("delivery-row")).to_have_count(25)
        expect(page.get_by_test_id("delivery-filter-subscription")).to_have_value(
            str(subscription.pk)
        )
        expect(page.get_by_test_id("delivery-filter-status")).to_have_value("false")

        page.get_by_test_id("trigger-delivery-job-list-pager-next").click()
        page.wait_for_url(
            f"**/studio/triggers/deliveries/?subscription={subscription.pk}"
            "&succeeded=false&jobs_page=2"
        )
        expect(page.get_by_test_id("delivery-job-row")).to_have_count(1)
        expect(page.get_by_test_id("delivery-row")).to_have_count(25)
        expect(page.get_by_test_id("trigger-delivery-job-list-pager-status")).to_have_text(
            "page 2 of 2"
        )
        expect(
            page.get_by_test_id("trigger-delivery-attempt-list-pager-status")
        ).to_have_text("page 1 of 2")

        page.get_by_test_id("trigger-delivery-attempt-list-pager-next").click()
        page.wait_for_url(
            f"**/studio/triggers/deliveries/?subscription={subscription.pk}"
            "&succeeded=false&jobs_page=2&attempts_page=2"
        )
        expect(page.get_by_test_id("delivery-job-row")).to_have_count(1)
        expect(page.get_by_test_id("delivery-row")).to_have_count(1)
        expect(page.get_by_test_id("delivery-filter-subscription")).to_have_value(
            str(subscription.pk)
        )
        expect(page.get_by_test_id("delivery-filter-status")).to_have_value("false")
        expect(page.get_by_test_id("deliveries-list")).not_to_contain_text(
            "pagination-other"
        )
        expect(page.get_by_test_id("delivery-succeeded")).to_have_count(0)

        page.goto(
            f"{django_server}/studio/triggers/deliveries/"
            f"?subscription={subscription.pk}&succeeded=false"
            "&jobs_page=malformed&attempts_page=999",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("trigger-delivery-job-list-pager-status")).to_have_text(
            "page 1 of 2"
        )
        expect(
            page.get_by_test_id("trigger-delivery-attempt-list-pager-status")
        ).to_have_text("page 2 of 2")
    finally:
        context.close()
