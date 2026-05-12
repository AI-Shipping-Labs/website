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


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_adds_content_source_and_first_sync_is_queued(
        django_server, browser):
    staff_email = "content-source-first-sync@test.com"
    _create_staff_user(staff_email)
    _reset_state()

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.goto(f"{django_server}/studio/content-sources/new/")
    page.select_option("#id_repo_name", "AI-Shipping-Labs/content-demo")
    page.fill("#id_webhook_secret", "manual-secret")
    page.get_by_role("button", name="Add content source").click()

    page.wait_for_url("**/studio/sync/")
    body = page.locator("body")
    assert "Added AI-Shipping-Labs/content-demo" in body.inner_text()
    assert "First sync queued" in body.inner_text()
    assert "manual-secret" in body.inner_text()

    card = page.locator('[data-repo-card][data-repo-name="AI-Shipping-Labs/content-demo"]')
    assert card.is_visible()
    assert card.get_attribute("data-status") == "queued"
    assert "queued" in card.inner_text()
    assert card.get_by_text("Sync now", exact=True).is_visible()
    assert card.get_by_text("Force resync", exact=True).is_visible()
    assert card.get_by_text("See in workers", exact=True).is_visible()

    from integrations.models import ContentSource, SyncLog

    source = ContentSource.objects.get(repo_name="AI-Shipping-Labs/content-demo")
    assert source.last_sync_status == "queued"
    assert SyncLog.objects.filter(source=source, status="queued").count() == 1
    connection.close()
    context.close()
