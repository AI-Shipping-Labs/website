"""Playwright E2E for onboarding-notification routing into Studio (issue #983).

The ``onboarding_submitted`` staff notification deep-links to the member's
CRM onboarding section (``/studio/crm/<id>/#onboarding``), auto-creating the
CRM record when needed -- never to the Django admin.
The ``plan_request`` notification routes to the locked request-preparation
flow rather than the generic Studio create-plan form.

These tests build the real notifications through the production service
helpers so the stored ``url`` is exactly what staff click in the bell and
on ``/notifications``.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Local-only: seeds DB rows and injects session cookies, so it cannot run
# against the deployed dev environment.
pytestmark = pytest.mark.local_only

STAFF_EMAIL = "onb983-staff@test.com"
NEWBIE_EMAIL = "newbie@test.com"
TRACKED_EMAIL = "tracked@test.com"


def _wipe_state():
    from accounts.models import User
    from crm.models import CRMRecord
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting
    from notifications.models import Notification
    from plans.models import Plan, Sprint

    Notification.objects.all().delete()
    CRMRecord.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    User.objects.exclude(is_staff=True).delete()
    # Drop any SITE_BASE_URL override a prior test in this module set so it
    # cannot leak into the shared Playwright DB used by other modules.
    IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
    clear_config_cache()
    connection.close()


def _point_site_base_url_at(server_url):
    """Override SITE_BASE_URL so stored absolute notification URLs resolve
    back to the local Playwright server instead of the production host.

    The ``onboarding_submitted`` notification stores an absolute URL built
    from ``site_base_url()``; without this override it would be the
    production host and clicking it would leave the test server.
    """
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key='SITE_BASE_URL', defaults={'value': server_url},
    )
    clear_config_cache()
    connection.close()


def _notify_onboarding(member):
    """Run the real onboarding fan-out so the stored url matches prod."""
    from crm.services.onboarding_notify import (
        notify_staff_onboarding_submitted,
    )

    notify_staff_onboarding_submitted(member)
    connection.close()


def _submit_onboarding_response(member, *, prompt="What are your goals?", answer="Ship an AI project"):
    """Create a submitted onboarding response with one visible Q/A row."""
    from django.utils import timezone

    from questionnaires.models import Answer, Questionnaire, Response, ResponseQuestion

    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug="onboarding-general",
        defaults={
            "title": "General onboarding",
            "purpose": "onboarding",
            "is_active": True,
        },
    )
    response = Response.objects.create(
        questionnaire=questionnaire,
        respondent=member,
        status="submitted",
        submitted_at=timezone.now(),
    )
    question = ResponseQuestion.objects.create(
        response=response,
        source_question=None,
        question_type="long_text",
        prompt=prompt,
        order=0,
    )
    Answer.objects.create(
        response=response,
        question=question,
        text_value=answer,
    )
    connection.close()
    return response


@pytest.mark.django_db(transaction=True)
class TestOnboardingNotificationRoutesToStudio:
    @pytest.mark.core
    def test_untracked_member_notification_auto_creates_crm_and_lands_on_answers(
        self, django_server, browser,
    ):
        """Bell click for an untracked onboarder lands on CRM onboarding."""
        from crm.models import CRMRecord

        _ensure_tiers()
        _wipe_state()
        _create_staff_user(STAFF_EMAIL)
        newbie = _create_user(
            NEWBIE_EMAIL, tier_slug="free", first_name="Newbie",
        )
        _submit_onboarding_response(newbie)
        _point_site_base_url_at(django_server)
        _notify_onboarding(newbie)
        record = CRMRecord.objects.get(user=newbie)
        record_pk = record.pk
        connection.close()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        # Open the bell and wait for the list to load.
        page.locator("#notification-bell-btn").click()
        page.locator("#notification-dropdown").wait_for(
            state="visible", timeout=5000,
        )
        page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        dropdown_text = page.locator("#notification-dropdown").inner_text()
        assert "Onboarding completed by" in dropdown_text
        assert "Tier: Free" in dropdown_text

        # The href targets CRM onboarding, not the Django admin.
        link = page.locator(
            f'#notification-list a[href*="/studio/crm/{record_pk}/#onboarding"]'
        )
        assert link.count() >= 1
        link.first.click()

        page.wait_for_url(f"**/studio/crm/{record_pk}/#onboarding", timeout=10000)
        assert f"/studio/crm/{record_pk}/#onboarding" in page.url
        assert "/admin/" not in page.url

        assert NEWBIE_EMAIL in page.locator("body").inner_text()
        onboarding = page.locator('[data-testid="crm-onboarding-section"]')
        assert onboarding.is_visible()
        assert "What are your goals?" in onboarding.inner_text()
        assert "Ship an AI project" in onboarding.inner_text()
        assert CRMRecord.objects.filter(user=newbie).count() == 1
        context.close()

    @pytest.mark.core
    def test_tracked_member_notification_lands_on_crm_detail(
        self, django_server, browser,
    ):
        """`/notifications` click for a tracked onboarder lands on CRM detail."""
        from crm.models import CRMRecord

        _ensure_tiers()
        _wipe_state()
        _create_staff_user(STAFF_EMAIL)
        tracked = _create_user(
            TRACKED_EMAIL, tier_slug="free", first_name="Tracked",
        )
        _submit_onboarding_response(
            tracked,
            prompt="Which outcome matters most?",
            answer="Finish the first sprint",
        )
        record = CRMRecord.objects.create(user=tracked)
        record_pk = record.pk
        connection.close()
        _point_site_base_url_at(django_server)
        _notify_onboarding(tracked)

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/notifications", wait_until="domcontentloaded",
        )

        link = page.locator(
            f'main a[href*="/studio/crm/{record_pk}/#onboarding"]'
        )
        assert link.count() >= 1
        href = link.first.get_attribute("href")
        assert "/admin/" not in href
        link.first.click()

        page.wait_for_url(f"**/studio/crm/{record_pk}/#onboarding", timeout=10000)
        assert f"/studio/crm/{record_pk}/#onboarding" in page.url
        assert "/admin/" not in page.url
        onboarding = page.locator('[data-testid="crm-onboarding-section"]')
        assert "Which outcome matters most?" in onboarding.inner_text()
        assert "Finish the first sprint" in onboarding.inner_text()
        context.close()

    @pytest.mark.core
    def test_no_onboarding_notification_href_targets_admin(
        self, django_server, browser,
    ):
        """Every onboarding href targets `/studio/...` and never `/admin/`."""
        _ensure_tiers()
        _wipe_state()
        _create_staff_user(STAFF_EMAIL)
        newbie = _create_user(NEWBIE_EMAIL, tier_slug="free")
        _point_site_base_url_at(django_server)
        _notify_onboarding(newbie)

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/notifications", wait_until="domcontentloaded",
        )

        # Locate the onboarding notification rows by their title text.
        onboarding_links = page.locator(
            'main a:has-text("Onboarding completed by")'
        )
        count = onboarding_links.count()
        assert count >= 1
        for i in range(count):
            href = onboarding_links.nth(i).get_attribute("href")
            assert href is not None
            assert "/studio/" in href
            assert "/admin/" not in href
        context.close()


@pytest.mark.django_db(transaction=True)
class TestPlanRequestNotificationRegression:
    @pytest.mark.core
    def test_plan_request_notification_routes_to_prepare_flow(
        self, django_server, browser,
    ):
        """Plan-request notification opens the locked preparation flow."""
        import datetime

        from plans.models import PlanRequest, Sprint
        from plans.views.sprints import (
            _create_staff_plan_request_notifications,
        )

        _ensure_tiers()
        _wipe_state()
        _create_staff_user(STAFF_EMAIL)
        member = _create_user(
            "planner-983@test.com", tier_slug="main", first_name="Planner",
        )
        sprint = Sprint.objects.create(
            name="Sprint 983",
            slug="sprint-983",
            # date-rot-ok: notification routing fixture; current sprint state is not under test.
            start_date=datetime.date(2026, 6, 1),
        )
        member_pk = member.pk
        sprint_pk = sprint.pk
        PlanRequest.objects.create(sprint=sprint, member=member)
        _create_staff_plan_request_notifications(member=member, sprint=sprint)
        connection.close()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        page.locator("#notification-bell-btn").click()
        page.locator("#notification-dropdown").wait_for(
            state="visible", timeout=5000,
        )
        page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        expected_path = (
            f"/studio/sprints/{sprint_pk}/plan-requests/{member_pk}/prepare/"
        )
        link = page.locator(f'#notification-list a[href*="{expected_path}"]')
        assert link.count() >= 1
        href = link.first.get_attribute("href")
        assert expected_path in href
        assert "/studio/plans/new" not in href
        assert "/admin/" not in href

        link.first.click()
        page.wait_for_url(f"**{expected_path}", timeout=10000)
        assert expected_path in page.url
        assert "/studio/plans/new" not in page.url
        assert "/admin/" not in page.url
        page.get_by_test_id("plan-request-summary").wait_for(state="visible")
        context.close()
