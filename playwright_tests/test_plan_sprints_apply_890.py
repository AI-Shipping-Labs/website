"""Playwright E2E for `#plan-sprints` auto-apply + reversal (issue #890, Phase 2).

LLM and Slack are stubbed: fixtures build captured threads, the auto-applied
``IngestedProgressEvent`` and its ``AppliedProgressChange`` rows directly in
the DB (the apply path is exercised at the Django-test layer), then these
tests drive the staff-only Studio surfaces + the undo controls.

Scenarios mirror the issue spec:
  - Staff sees the auto-applied summary/blockers + undo controls on a plan.
  - Staff undoes a single change; only that plan item reverts.
  - Staff undoes a whole event; manual completions stay done.
  - A thread with no auto-apply renders as Phase 1 (no apply controls).
  - A non-staff member cannot POST to an undo URL.

Usage:
    uv run pytest playwright_tests/test_plan_sprints_apply_890.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

CHANNEL = "C_E2E_PLANSPRINTS_890"


def _make_plan(email, slack_user_id, *, active=True):
    from django.db import connection

    from crm.models import CRMRecord
    from plans.models import Plan, Sprint, Week

    user = create_user(email)
    user.slack_user_id = slack_user_id
    user.save(update_fields=["slack_user_id"])
    record, _ = CRMRecord.objects.get_or_create(user=user)
    sprint = Sprint.objects.create(
        name=f"Sprint {email}",
        slug=f"sprint-{slack_user_id.lower()}",
        # date-rot-ok: Slack ingest apply fixture; active/completed status drives behavior.
        start_date=datetime.date(2026, 5, 1),
        status="active" if active else "completed",
    )
    plan = Plan.objects.create(member=user, sprint=sprint)
    week = Week.objects.create(plan=plan, week_number=1)
    connection.close()
    return user, record, plan, week


def _captured_thread(user, plan, ts):
    from django.utils import timezone

    from crm.models import SlackMessage, SlackThread

    thread = SlackThread.objects.create(
        channel_id=CHANNEL, thread_ts=ts, slack_user_id=user.slack_user_id,
        member=user, plan=plan, posted_at=timezone.now(),
    )
    SlackMessage.objects.create(
        thread=thread, ts=ts, slack_user_id=user.slack_user_id,
        text="Finished my work this week", posted_at=timezone.now(),
        is_root=True,
    )
    return thread


def _event_marking(thread, plan, *, summary, blockers, items):
    """Create an event + a flip change per (model_instance, kind)."""
    from django.utils import timezone

    from crm.models import AppliedProgressChange, IngestedProgressEvent

    event = IngestedProgressEvent.objects.create(
        thread=thread, plan=plan, summary=summary, blockers=blockers,
        source_message_ts=thread.thread_ts,
    )
    now = timezone.now()
    for item, kind in items:
        item.done_at = now
        item.save(update_fields=["done_at"])
        kwargs = {"event": event, "item_kind": kind, "previous_done_at": None}
        kwargs[kind] = item
        AppliedProgressChange.objects.create(**kwargs)
    return event


@pytest.mark.django_db(transaction=True)
class TestAutoAppliedVisibleOnCRM:
    def test_summary_blockers_and_undo_controls(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from plans.models import Checkpoint

            create_staff_user("admin-890a@test.com")
            user, record, plan, week = _make_plan("m-890a@test.com", "U_890A")
            cp = Checkpoint.objects.create(week=week, description="Build pipeline")
            thread = _captured_thread(user, plan, "1700100000.000100")
            _event_marking(
                thread, plan,
                summary="Wrapped up the pipeline.",
                blockers=["Waiting on data access"],
                items=[(cp, "checkpoint")],
            )
            plan_id = plan.pk

        context = auth_context(browser, "admin-890a@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="crm-slack-autoapply"]'
            ).count() == 1
            body = page.content()
            assert "Wrapped up the pipeline." in body
            assert "Waiting on data access" in body
            assert page.locator(
                '[data-testid="crm-slack-autoapply-change-undo"]'
            ).count() == 1
            assert page.locator(
                '[data-testid="crm-slack-autoapply-undo-all"]'
            ).count() == 1
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestSingleChangeUndo:
    def test_undo_one_change_reverts_only_that_item(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from plans.models import Checkpoint, Deliverable

            create_staff_user("admin-890b@test.com")
            user, record, plan, week = _make_plan("m-890b@test.com", "U_890B")
            cp = Checkpoint.objects.create(week=week, description="Checkpoint X")
            deliv = Deliverable.objects.create(plan=plan, description="Deliverable Y")
            thread = _captured_thread(user, plan, "1700100100.000100")
            _event_marking(
                thread, plan, summary="Two done.", blockers=[],
                items=[(cp, "checkpoint"), (deliv, "deliverable")],
            )
            plan_id = plan.pk
            cp_id, deliv_id = cp.pk, deliv.pk

        context = auth_context(browser, "admin-890b@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            # Expand the thread so the auto-apply controls become visible.
            page.locator(
                '[data-testid="crm-slack-thread"] summary'
            ).first.click()
            # Undo the FIRST change (the checkpoint).
            page.locator(
                '[data-testid="crm-slack-autoapply-change-undo"]'
            ).first.click()
            page.wait_for_load_state("domcontentloaded")

            with django_db_blocker.unblock():
                from plans.models import Checkpoint, Deliverable

                assert Checkpoint.objects.get(pk=cp_id).done_at is None
                # The deliverable stays done.
                assert Deliverable.objects.get(pk=deliv_id).done_at is not None
            # The event block still renders (summary survives).
            assert "Two done." in page.content()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestEventUndoLeavesManualIntact:
    def test_undo_all_keeps_manual_completion(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from django.utils import timezone

            from plans.models import Checkpoint, NextStep

            create_staff_user("admin-890c@test.com")
            user, record, plan, week = _make_plan("m-890c@test.com", "U_890C")
            cp = Checkpoint.objects.create(week=week, description="Auto CP")
            cp2 = Checkpoint.objects.create(week=week, description="Auto CP 2")
            # Manually completed BEFORE ingest — must survive an event undo.
            manual = NextStep.objects.create(plan=plan, description="Manual step")
            manual.done_at = timezone.now()
            manual.save(update_fields=["done_at"])
            thread = _captured_thread(user, plan, "1700100200.000100")
            _event_marking(
                thread, plan, summary="Auto two.", blockers=[],
                items=[(cp, "checkpoint"), (cp2, "checkpoint")],
            )
            plan_id = plan.pk
            cp_id, cp2_id, manual_id = cp.pk, cp2.pk, manual.pk

        context = auth_context(browser, "admin-890c@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            # Expand the thread so the auto-apply controls become visible.
            page.locator(
                '[data-testid="crm-slack-thread"] summary'
            ).first.click()
            page.locator(
                '[data-testid="crm-slack-autoapply-undo-all"]'
            ).first.click()
            page.wait_for_load_state("domcontentloaded")

            with django_db_blocker.unblock():
                from plans.models import Checkpoint, NextStep

                assert Checkpoint.objects.get(pk=cp_id).done_at is None
                assert Checkpoint.objects.get(pk=cp2_id).done_at is None
                # The manual completion is untouched.
                assert NextStep.objects.get(pk=manual_id).done_at is not None
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestThreadWithoutEvent:
    def test_phase1_view_no_apply_controls(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            create_staff_user("admin-890d@test.com")
            user, record, plan, week = _make_plan("m-890d@test.com", "U_890D")
            _captured_thread(user, plan, "1700100300.000100")
            plan_id = plan.pk

        context = auth_context(browser, "admin-890d@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="crm-slack-thread"]'
            ).count() == 1
            assert page.locator(
                '[data-testid="crm-slack-autoapply"]'
            ).count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffCannotUndo:
    def test_member_post_to_undo_is_denied(
        self, django_server, django_db_blocker, browser, settings,
    ):
        with django_db_blocker.unblock():
            from plans.models import Checkpoint

            create_staff_user("admin-890e@test.com")
            user, record, plan, week = _make_plan("m-890e@test.com", "U_890E")
            cp = Checkpoint.objects.create(week=week, description="CP")
            thread = _captured_thread(user, plan, "1700100400.000100")
            event = _event_marking(
                thread, plan, summary="x", blockers=[],
                items=[(cp, "checkpoint")],
            )
            # A logged-in NON-staff member.
            create_user("nonstaff-890e@test.com")
            event_id, cp_id = event.pk, cp.pk

        context = auth_context(browser, "nonstaff-890e@test.com")
        try:
            page = context.new_page()
            # Need a CSRF token first: visit any page that sets the cookie.
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            resp = context.request.post(
                f"{django_server}/studio/crm/slack-progress/{event_id}/undo",
                headers={"X-CSRFToken": _csrf_from(context)},
            )
            # Non-staff is denied (redirect to login or 403/404), never 200-OK.
            assert resp.status in (302, 401, 403, 404)

            with django_db_blocker.unblock():
                from plans.models import Checkpoint

                # The plan item is unchanged — no reversal happened.
                assert Checkpoint.objects.get(pk=cp_id).done_at is not None
        finally:
            context.close()


def _csrf_from(context):
    for cookie in context.cookies():
        if cookie["name"] == "csrftoken":
            return cookie["value"]
    return ""
