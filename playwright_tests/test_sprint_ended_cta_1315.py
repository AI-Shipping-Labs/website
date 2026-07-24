"""Playwright coverage for ended-sprint CTAs on /sprints (#1315).

An anonymous visitor must not be lured into joining a finished sprint: the
card CTA reads ``View sprint`` (not ``Log in to join``) and lands on the
detail page, which explains the sprint has ended.
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    ensure_site_config_tiers,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]

SCREENSHOT_DIR = (
    Path(__file__).resolve().parents[1] / ".tmp" / "aisl-issue-1315-screenshots"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _clear_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(name, slug, *, start_date, duration_weeks=4,
                   status="completed", min_tier_level=30):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date,
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _card_for_slug(page, slug):
    return page.locator(
        f'[data-testid="sprints-sprint-card"]:has(a[href$="/sprints/{slug}"])'
    )


def test_anonymous_visitor_cannot_be_lured_into_joining_finished_sprint(
    django_server, page, django_db_blocker
):
    today = datetime.date.today()
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()
        _clear_sprints()
        _create_sprint(
            "Finished Premium Sprint",
            "finished-premium-sprint",
            start_date=today - datetime.timedelta(days=56),
        )

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    card = _card_for_slug(page, "finished-premium-sprint")
    assert "ENDED" in card.inner_text()

    cta = card.locator('[data-testid="sprints-sprint-cta"]')
    assert cta.inner_text().strip().startswith("View sprint")
    assert "Log in to join" not in card.inner_text()
    _shot(page, "01-anon-ended-card")

    cta.click()
    page.wait_for_url("**/sprints/finished-premium-sprint")
    detail_text = page.locator("body").inner_text()
    assert "This sprint has ended and is no longer open to join." in detail_text
    _shot(page, "02-anon-ended-detail")
