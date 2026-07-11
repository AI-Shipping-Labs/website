"""Playwright coverage for weekly sprint cadence notifications (#1200)."""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection
from django.utils import timezone

pytestmark = pytest.mark.local_only


def _active_sprint(slug="pw-sprint-cadence", start=None):
    from plans.models import Sprint

    return Sprint.objects.create(
        name="Playwright Sprint",
        slug=slug,
        start_date=start or datetime.date(2026, 5, 1),
        duration_weeks=4,
        status="active",
    )


def _shared_plan(email="sprint-pw@test.com", *, sprint=None):
    from plans.models import Plan

    user = _create_user(email, tier_slug="main", email_verified=True)
    sprint = sprint or _active_sprint()
    plan = Plan.objects.create(
        member=user,
        sprint=sprint,
        shared_at=timezone.now(),
    )
    return user, plan


def _week(plan, number, *, position, theme=""):
    from plans.models import Week

    return Week.objects.create(
        plan=plan,
        week_number=number,
        position=position,
        theme=theme,
    )


def _open_bell(page):
    page.locator("#notification-bell-btn").click()
    dropdown = page.locator("#notification-dropdown")
    dropdown.wait_for(state="visible", timeout=5000)
    return dropdown


@pytest.mark.django_db(transaction=True)
class TestSprintCadenceNotifications:
    @pytest.mark.core
    def test_week_start_notification_opens_the_right_week(
        self,
        django_server,
        browser,
    ):
        from plans.models import Checkpoint
        from plans.services.sprint_cadence import send_sprint_cadence_notifications

        user, plan = _shared_plan("week-start-pw@test.com")
        _week(plan, 1, position=0, theme="Prep")
        _week(plan, 2, position=1, theme="Build")
        week3 = _week(plan, 3, position=2, theme="Ship prototype")
        for index in range(4):
            cp = Checkpoint.objects.create(
                week=week3,
                description=f"Week 3 checkpoint {index}",
            )
            if index < 2:
                cp.done_at = timezone.now()
                cp.save(update_fields=["done_at"])
        send_sprint_cadence_notifications(today=datetime.date(2026, 5, 15))
        connection.close()

        context = _auth_context(browser, user.email)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_bell(page)

        page.get_by_text("Week 3 is ready: Ship prototype").click()

        page.wait_for_url(f"**/sprints/{plan.sprint.slug}/plan/{plan.pk}#week-{week3.pk}")
        assert page.locator(f"#week-{week3.pk}").is_visible()
        assert page.locator(f"#week-{week3.pk}").get_by_text(
            "Week 3: Ship prototype"
        ).is_visible()

    @pytest.mark.core
    def test_week_note_prompt_opens_note_form_and_suppresses_second_prompt(
        self,
        django_server,
        browser,
    ):
        from notifications.models import Notification
        from plans.services.sprint_cadence import send_sprint_cadence_notifications

        user, plan = _shared_plan("week-note-pw@test.com")
        week2 = _week(plan, 2, position=1, theme="Validate")
        send_sprint_cadence_notifications(today=datetime.date(2026, 5, 14))
        connection.close()

        context = _auth_context(browser, user.email)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_bell(page)
        page.get_by_text("Write your Week 2 sprint note").click()
        page.wait_for_url(f"**/sprints/{plan.sprint.slug}/plan/{plan.pk}#week-{week2.pk}")

        week_panel = page.locator(f"#week-{week2.pk}")
        week_panel.locator('[data-testid="plan-week-note-add-textarea"]').fill(
            "Shipped the validation pass."
        )
        week_panel.locator('[data-testid="plan-week-note-add-submit"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="plan-week-note-body"]').get_by_text(
            "Shipped the validation pass."
        ).is_visible()

        connection.close()
        send_sprint_cadence_notifications(today=datetime.date(2026, 5, 14))
        assert Notification.objects.filter(
            user=user,
            notification_type="week_note_prompt",
        ).count() == 1
        connection.close()

    @pytest.mark.core
    def test_account_toggle_disables_sprint_email_but_keeps_bell_notification(
        self,
        django_server,
        browser,
    ):
        from email_app.models import EmailLog
        from notifications.models import Notification
        from plans.services.sprint_cadence import send_sprint_cadence_notifications

        user, plan = _shared_plan("toggle-sprint-pw@test.com")
        _week(plan, 1, position=0, theme="Start")
        connection.close()

        context = _auth_context(browser, user.email)
        page = context.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        page.locator('[data-testid="sprint-cadence-emails-toggle"]').click()
        status = page.locator('[data-testid="sprint-cadence-emails-status"]')
        status.get_by_text("Sprint reminders turned off.").wait_for()

        connection.close()
        send_sprint_cadence_notifications(today=datetime.date(2026, 5, 1))

        assert Notification.objects.filter(
            user=user,
            notification_type="sprint_week_start",
        ).count() == 1
        assert EmailLog.objects.filter(
            user=user,
            email_type="sprint_week_start",
        ).count() == 0
        connection.close()

    @pytest.mark.core
    def test_slack_progress_notification_opens_callout_and_undoes_updates(
        self,
        django_server,
        browser,
    ):
        from crm.models import (
            AppliedProgressChange,
            IngestedProgressEvent,
            SlackMessage,
            SlackThread,
        )
        from notifications.models import Notification
        from plans.models import Checkpoint
        from plans.services.sprint_cadence import create_slack_progress_delivery

        user, plan = _shared_plan("slack-undo-pw@test.com")
        week = _week(plan, 1, position=0, theme="Start")
        cp = Checkpoint.objects.create(
            week=week,
            description="Auto-applied checkpoint",
            done_at=timezone.now(),
        )
        thread = SlackThread.objects.create(
            channel_id="C_PLAN",
            thread_ts="1700000000.000500",
            slack_user_id="U_MEMBER",
            member=user,
            plan=plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread,
            ts="1700000000.000500",
            slack_user_id="U_MEMBER",
            text="Done",
            posted_at=timezone.now(),
            is_root=True,
        )
        event = IngestedProgressEvent.objects.create(
            thread=thread,
            plan=plan,
            summary="Progress",
            source_message_ts="1700000000.000500",
        )
        change = AppliedProgressChange.objects.create(
            event=event,
            item_kind="checkpoint",
            checkpoint=cp,
            previous_done_at=None,
        )
        create_slack_progress_delivery(event, [change])
        connection.close()

        context = _auth_context(browser, user.email)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_bell(page)
        page.get_by_text("We marked 1 item done from your Slack update.").click()
        page.wait_for_url(f"**/sprints/{plan.sprint.slug}/plan/{plan.pk}?progress_event={event.pk}#slack-progress")

        callout = page.locator('[data-testid="slack-progress-callout"]')
        assert callout.get_by_text("Auto-applied checkpoint").is_visible()
        callout.locator('[data-testid="slack-progress-undo"]').click()
        page.wait_for_load_state("domcontentloaded")

        connection.close()
        cp.refresh_from_db()
        assert cp.done_at is None
        assert Notification.objects.filter(
            user=user,
            notification_type="slack_progress",
        ).count() == 1
        connection.close()
