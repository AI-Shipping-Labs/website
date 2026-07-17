"""Playwright coverage for Studio sprint public preview links (#1049)."""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _clear_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint():
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name="Operator Sprint",
        slug="operator-sprint",
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        duration_weeks=6,
        status="active",
        min_tier_level=0,
    )
    connection.close()
    return sprint


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_operator_opens_public_sprint_from_studio(
    django_server, browser,
):
    _clear_sprints()
    _ensure_tiers()
    _create_staff_user("studio-sprint-preview@test.com")
    sprint = _create_sprint()

    context = _auth_context(browser, "studio-sprint-preview@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/sprints/{sprint.pk}/",
        wait_until="domcontentloaded",
    )

    action_row = page.locator('[data-testid="studio-header-actions"]')
    assert action_row.count() == 1
    page.get_by_label("More actions").click()

    view_on_site = action_row.locator('[data-testid="view-on-site"]')
    assert view_on_site.count() == 1
    assert view_on_site.inner_text() == "View on site"
    assert view_on_site.get_attribute("href") == sprint.get_absolute_url()
    assert view_on_site.get_attribute("target") == "_blank"
    assert view_on_site.get_attribute("rel") == "noopener noreferrer"

    with page.expect_popup() as popup_info:
        view_on_site.click()
    public_page = popup_info.value
    public_page.wait_for_load_state("domcontentloaded")

    assert public_page.url.endswith(sprint.get_absolute_url())
    assert (
        public_page.locator('[data-testid="sprint-detail-name"]').inner_text()
        == sprint.name
    )
    assert page.url.endswith(f"/studio/sprints/{sprint.pk}/")

    context.close()
