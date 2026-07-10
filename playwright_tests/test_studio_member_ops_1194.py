"""Playwright coverage for Studio member-ops links/actions (issue #1194)."""

import datetime
import os
from pathlib import Path

import pytest

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
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.local_only

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1194")


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _reset(staff_email):
    from accounts.models import EmailAlias, User
    from community.models import CommunityAuditLog
    from email_app.models import EmailCampaign, EmailLog, SesEvent
    from events.models import Event, EventRegistration
    from plans.models import Plan, Sprint, SprintEnrollment

    CommunityAuditLog.objects.all().delete()
    EmailAlias.objects.all().delete()
    SesEvent.objects.all().delete()
    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _seed_member_ops():
    from accounts.models import User
    from email_app.models import EmailCampaign, EmailLog, SesEvent
    from events.models import Event, EventRegistration
    from plans.models import Plan, Sprint, SprintEnrollment

    member = User.objects.create_user(
        email="member-ops-1194@test.com",
        password="pw",
        first_name="Member",
        last_name="Ops",
        email_verified=True,
    )
    other = User.objects.create_user(
        email="other-ops-1194@test.com",
        password="pw",
        email_verified=True,
    )
    today = datetime.date.today()
    current = Sprint.objects.create(
        name="1194 Current Sprint",
        slug="1194-current-sprint",
        start_date=today - datetime.timedelta(days=4),
        duration_weeks=6,
    )
    future = Sprint.objects.create(
        name="1194 Needs Plan Sprint",
        slug="1194-needs-plan-sprint",
        start_date=today + datetime.timedelta(days=20),
        duration_weeks=6,
    )
    plan = Plan.objects.create(member=member, sprint=current, goal="Jump to plan")
    SprintEnrollment.objects.create(user=member, sprint=future)

    upcoming = Event.objects.create(
        title="1194 Upcoming Event",
        slug="1194-upcoming-event",
        start_datetime=timezone.now() + datetime.timedelta(days=5),
        status="upcoming",
    )
    past = Event.objects.create(
        title="1194 Past Event",
        slug="1194-past-event",
        start_datetime=timezone.now() - datetime.timedelta(days=5),
        status="completed",
    )
    EventRegistration.objects.create(event=upcoming, user=member)
    EventRegistration.objects.create(
        event=past,
        user=member,
        joined_at=timezone.now() - datetime.timedelta(days=5),
    )

    draft = EmailCampaign.objects.create(
        subject="1194 Draft Campaign",
        body="Hi",
        status="draft",
        target_min_level=0,
    )
    sent = EmailCampaign.objects.create(
        subject="1194 Sent Campaign",
        body="Hi",
        status="sent",
    )
    log = EmailLog.objects.create(
        campaign=sent,
        user=member,
        email_type="campaign",
        bounced_at=timezone.now(),
        bounce_type="Permanent",
        bounce_diagnostic="smtp 550 missing",
        ses_message_id="1194-sent-message",
    )
    SesEvent.objects.create(
        event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
        message_id="1194-sns-message",
        raw_payload={"ok": True},
        recipient_email=member.email,
        user=member,
        email_log=log,
    )
    ids = {
        "member": member.pk,
        "other": other.pk,
        "plan": plan.pk,
        "future": future.pk,
        "upcoming": upcoming.pk,
        "draft": draft.pk,
        "sent": sent.pk,
    }
    connection.close()
    return ids


@pytest.mark.django_db(transaction=True)
class TestStudioMemberOps1194:
    def test_user_detail_plan_sprint_and_event_links(self, django_server, browser):
        _ensure_tiers()
        staff_email = "pw-1194-staff@test.com"
        _create_staff_user(staff_email)
        _reset(staff_email)
        ids = _seed_member_ops()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP)
        page.goto(f"{django_server}/studio/users/{ids['member']}/")

        assert page.get_by_test_id("user-plans-sprints-section").is_visible()
        page.get_by_test_id("user-plan-link").click()
        page.wait_for_url(f"**/studio/plans/{ids['plan']}/")

        page.goto(f"{django_server}/studio/users/{ids['member']}/")
        page.get_by_test_id("user-create-plan-link").click()
        page.wait_for_url(f"**/studio/plans/new?user={ids['member']}&sprint={ids['future']}")

        page.goto(f"{django_server}/studio/users/{ids['member']}/")
        page.get_by_test_id("user-event-registration-edit-link").first.click()
        page.wait_for_url("**/studio/events/*/edit")
        _capture(page, "user-detail-links")

    def test_event_attendee_links_desktop_and_mobile(self, django_server, browser):
        _ensure_tiers()
        staff_email = "pw-1194-event-staff@test.com"
        _create_staff_user(staff_email)
        _reset(staff_email)
        ids = _seed_member_ops()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP)
        page.goto(f"{django_server}/studio/events/{ids['upcoming']}/edit")
        page.locator("#registrations-filter").fill("member-ops-1194")
        page.get_by_test_id("registration-email-link").click()
        page.wait_for_url(f"**/studio/users/{ids['member']}/")

        page.goto(f"{django_server}/studio/events/{ids['upcoming']}/edit")
        page.set_viewport_size(MOBILE)
        page.get_by_test_id("registration-card-email-link").click()
        page.wait_for_url(f"**/studio/users/{ids['member']}/")
        _capture(page, "event-attendee-mobile")

    def test_campaign_recipient_trace_and_member_actions(self, django_server, browser):
        _ensure_tiers()
        staff_email = "pw-1194-actions-staff@test.com"
        _create_staff_user(staff_email)
        _reset(staff_email)
        ids = _seed_member_ops()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(DESKTOP)

        page.goto(f"{django_server}/studio/campaigns/{ids['draft']}/")
        page.get_by_test_id("campaign-recipients-link").click()
        page.wait_for_url(f"**/studio/campaigns/{ids['draft']}/recipients/")
        expect(page.get_by_test_id("campaign-recipient-mode")).to_contain_text(
            "Draft preview"
        )

        page.goto(f"{django_server}/studio/campaigns/{ids['sent']}/recipients/")
        expect(page.get_by_test_id("campaign-recipient-disposition")).to_contain_text(
            "Bounced"
        )
        expect(page.get_by_test_id("campaign-recipient-diagnostic")).to_contain_text(
            "smtp 550 missing"
        )
        page.get_by_test_id("campaign-recipient-ses-link").click()
        page.wait_for_url(f"**/studio/ses-events/?campaign={ids['sent']}")

        page.goto(f"{django_server}/studio/users/{ids['member']}/")
        page.locator(
            f'form[action="/studio/users/{ids["member"]}/deliverability/permanent"] input[name="reason"]'
        ).fill("hard bounce observed")
        page.get_by_test_id("user-deliverability-permanent").click()
        expect(page.get_by_test_id("user-detail-bounce-state")).to_contain_text(
            "Permanent"
        )

        page.get_by_test_id("user-alias-input").fill("relay1194@example.com")
        page.get_by_test_id("user-alias-note").fill("relay")
        page.get_by_test_id("user-alias-add-submit").click()
        expect(page.get_by_test_id("user-alias-row")).to_contain_text(
            "relay1194@example.com"
        )
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_test_id("user-alias-remove").click()
        assert page.get_by_test_id("user-aliases-empty").is_visible()
        _capture(page, "campaign-and-actions")
