"""Playwright coverage for auto-queueing first sync on ContentSource create."""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.core.cache import cache  # noqa: E402
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _reset_state():
    from django_q.models import OrmQ

    from integrations.models import ContentSource, SyncLog
    from integrations.services.github import INSTALLATION_REPOS_CACHE_KEY

    SyncLog.objects.all().delete()
    ContentSource.objects.all().delete()
    OrmQ.objects.all().delete()
    cache.set(
        INSTALLATION_REPOS_CACHE_KEY,
        [{
            "full_name": "AI-Shipping-Labs/content-demo",
            "private": False,
            "default_branch": "main",
        }],
    )
    connection.close()


def _seed_blank_secret_source():
    from integrations.models import ContentSource, SyncLog

    SyncLog.objects.all().delete()
    ContentSource.objects.all().delete()
    ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/content",
        webhook_secret="",
        is_private=True,
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_staff_adds_content_source_with_blank_secret_and_first_sync_is_queued(
        django_server, browser):
    staff_email = "content-source-first-sync@test.com"
    _create_staff_user(staff_email)
    _reset_state()

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.goto(f"{django_server}/studio/content-sources/new/")
    page.select_option("#id_repo_name", "AI-Shipping-Labs/content-demo")
    page.get_by_role("button", name="Add content source").click()

    page.wait_for_url("**/studio/sync/")

    card = page.locator('[data-repo-card][data-repo-name="AI-Shipping-Labs/content-demo"]')
    assert card.is_visible()
    assert card.get_attribute("data-status") == "queued"
    assert "queued" in card.inner_text()
    assert card.get_by_text("Sync now", exact=True).is_visible()
    assert card.get_by_text("Force resync", exact=True).is_visible()
    assert card.get_by_text("See in workers", exact=True).is_visible()

    from integrations.models import ContentSource, SyncLog

    source = ContentSource.objects.get(repo_name="AI-Shipping-Labs/content-demo")
    body_text = page.locator("body").inner_text()
    assert "Added AI-Shipping-Labs/content-demo" in body_text
    assert "First sync queued" in body_text
    assert source.webhook_secret
    assert len(source.webhook_secret) >= 32
    assert source.webhook_secret in body_text
    assert source.last_sync_status == "queued"
    assert SyncLog.objects.filter(source=source, status="queued").count() == 1
    connection.close()
    context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_sync_dashboard_warns_when_content_source_secret_is_missing(
        django_server, browser):
    staff_email = "content-source-missing-secret@test.com"
    _create_staff_user(staff_email)
    _seed_blank_secret_source()

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.goto(f"{django_server}/studio/sync/", wait_until="domcontentloaded")

    card = page.locator('[data-repo-card][data-repo-name="AI-Shipping-Labs/content"]')
    assert card.is_visible()
    warning = card.locator('[data-testid="webhook-secret-warning"]')
    assert warning.is_visible()
    assert (
        "GitHub webhooks are blocked until a webhook secret is configured"
        in warning.inner_text()
    )
    assert card.get_by_text("Sync now", exact=True).is_visible()
    assert card.get_by_text("Force resync", exact=True).is_visible()

    context.close()
