"""Playwright E2E for `#plan-sprints` ingest surfacing (issue #889, Phase 1).

Slack is stubbed: each test drives the real ``ingest_plan_sprints`` task
with fixture payloads (patching ``SlackCommunityService`` at the service
boundary), then asserts on the staff-only Studio surfaces.

Scenarios mirror the issue spec:
  - Staff reviews a member's full Slack thread after the daily ingest.
  - New replies on an old thread show up after the next day's run.
  - Staff sees an update from someone we could not match to a member.
  - Ingest no-ops when Slack is not configured (empty state, not broken).
  - A matched update is linked to the member's active-sprint plan.

Usage:
    uv run pytest playwright_tests/test_plan_sprints_ingest_889.py -v
"""

import datetime
import os
from unittest.mock import patch

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

CHANNEL = "C_E2E_PLANSPRINTS"

SLACK_SETTINGS = dict(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-e2e",
    SLACK_ENVIRONMENT="test",
    SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID=CHANNEL,
)


def _msg(ts, user, text, thread_ts=None):
    m = {"ts": ts, "user": user, "text": text}
    if thread_ts:
        m["thread_ts"] = thread_ts
    return m


class _FakeSlackService:
    def __init__(self, history, replies, display=None):
        self._history = history
        self._replies = replies
        self._display = display or {}

    def fetch_conversation_history(self, channel_id, oldest=None, limit=200):
        return list(self._history)

    def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
        return list(self._replies.get(thread_ts, []))

    def lookup_user_display_name(self, slack_user_id):
        return self._display.get(slack_user_id, "")

    def get_message_permalink(self, channel_id, message_ts):
        return f"https://slack.example/archives/{channel_id}/p{message_ts}"


def _run_ingest(service):
    """Run the real ingest task against a stub Slack service."""
    from crm.tasks import ingest_plan_sprints

    with patch(
        "crm.tasks.ingest_plan_sprints.SlackCommunityService",
        return_value=service,
    ):
        return ingest_plan_sprints()


def _make_member_with_plan(email, slack_user_id, *, active=True):
    """Create a tracked member enrolled in an (optionally active) sprint plan."""
    from django.db import connection

    from crm.models import CRMRecord
    from plans.models import Plan, Sprint

    user = create_user(email)
    user.slack_user_id = slack_user_id
    user.save(update_fields=["slack_user_id"])
    record, _ = CRMRecord.objects.get_or_create(user=user)
    sprint = Sprint.objects.create(
        name=f"Sprint {email}",
        slug=f"sprint-{slack_user_id.lower()}",
        start_date=datetime.date(2026, 5, 1),
        status="active" if active else "completed",
    )
    plan = Plan.objects.create(member=user, sprint=sprint)
    connection.close()
    return user, record, plan


@pytest.mark.django_db(transaction=True)
class TestStaffReviewsFullThread:
    def test_full_thread_visible_as_member_note_on_crm_record(
        self, django_server, django_db_blocker, browser, settings,
    ):
        for key, value in SLACK_SETTINGS.items():
            setattr(settings, key, value)

        with django_db_blocker.unblock():
            create_staff_user("admin-889a@test.com")
            user, record, _ = _make_member_with_plan(
                "thread-889a@test.com", "U_889A",
            )
            root = _msg("1700000000.000100", "U_889A", "Finished week 1 work")
            r1 = _msg("1700000000.000200", "U_889B", "Nice progress!",
                      thread_ts="1700000000.000100")
            r2 = _msg("1700000000.000300", "U_889A", "Thanks, onto week 2",
                      thread_ts="1700000000.000100")
            service = _FakeSlackService(
                history=[root],
                replies={"1700000000.000100": [root, r1, r2]},
                display={"U_889A": "Aaron", "U_889B": "Bea"},
            )
            run = _run_ingest(service)
            assert run.threads_persisted == 1
            crm_id = record.pk

        context = auth_context(browser, "admin-889a@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/crm/{crm_id}/",
                wait_until="domcontentloaded",
            )
            assert page.locator('[data-testid="crm-slack-updates-section"]').count() == 0
            notes = page.locator('[data-testid="internal-notes"]')
            assert notes.locator('[data-testid="member-note-tag"]').filter(
                has_text="slack"
            ).is_visible()
            assert notes.locator('[data-testid="member-note-tag"]').filter(
                has_text="plan-sprints"
            ).is_visible()
            body = page.content()
            assert "Finished week 1 work" in body
            assert "Nice progress!" in body
            assert "Thanks, onto week 2" in body
            assert "Aaron" in body
            assert "Bea" in body
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNewReplyShowsAfterNextRun:
    def test_new_reply_appears_after_second_ingest(
        self, django_server, django_db_blocker, browser, settings,
    ):
        for key, value in SLACK_SETTINGS.items():
            setattr(settings, key, value)

        with django_db_blocker.unblock():
            create_staff_user("admin-889b@test.com")
            user, record, _ = _make_member_with_plan(
                "thread-889b@test.com", "U_889C",
            )
            root = _msg("1700001000.000100", "U_889C", "Day one update")
            r1 = _msg("1700001000.000200", "U_889D", "Keep going",
                      thread_ts="1700001000.000100")
            # Day 1: root + 1 reply.
            _run_ingest(_FakeSlackService(
                history=[root],
                replies={"1700001000.000100": [root, r1]},
            ))
            crm_id = record.pk

        with django_db_blocker.unblock():
            # Day 2: a new reply appeared on the same thread.
            root = _msg("1700001000.000100", "U_889C", "Day one update")
            r1 = _msg("1700001000.000200", "U_889D", "Keep going",
                      thread_ts="1700001000.000100")
            r2 = _msg("1700001000.000300", "U_889E", "Added a second reply",
                      thread_ts="1700001000.000100")
            run2 = _run_ingest(_FakeSlackService(
                history=[root],
                replies={"1700001000.000100": [root, r1, r2]},
            ))
            assert run2.replies_added == 1

        context = auth_context(browser, "admin-889b@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/crm/{crm_id}/",
                wait_until="domcontentloaded",
            )
            assert page.locator('[data-testid="crm-slack-updates-section"]').count() == 0
            assert page.locator('[data-testid="internal-notes"] li').filter(
                has_text="Day one update"
            ).count() == 1
            body = page.content()
            assert "Added a second reply" in body
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestUnmatchedUpdateVisibleToStaff:
    def test_unmatched_thread_on_review_surface(
        self, django_server, django_db_blocker, browser, settings,
    ):
        for key, value in SLACK_SETTINGS.items():
            setattr(settings, key, value)

        with django_db_blocker.unblock():
            create_staff_user("admin-889c@test.com")
            root = _msg("1700002000.000100", "U_STRANGER", "Update from a non-member")
            _run_ingest(_FakeSlackService(
                history=[root],
                replies={"1700002000.000100": [root]},
                display={"U_STRANGER": "Mystery Person"},
            ))

        context = auth_context(browser, "admin-889c@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/crm/slack-ingest",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="slack-ingest-unmatched-thread"]'
            ).count() == 1
            body = page.content()
            assert "Update from a non-member" in body
            assert "Unmatched" in body
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNoopWhenSlackNotConfigured:
    def test_empty_state_when_slack_disabled(
        self, django_server, django_db_blocker, browser, settings,
    ):
        settings.SLACK_ENABLED = False

        with django_db_blocker.unblock():
            from crm.models import SlackThread
            from crm.tasks import ingest_plan_sprints

            create_staff_user("admin-889d@test.com")
            user, record, _ = _make_member_with_plan(
                "thread-889d@test.com", "U_889F",
            )
            result = ingest_plan_sprints()
            assert result is None
            assert SlackThread.objects.count() == 0
            crm_id = record.pk

        context = auth_context(browser, "admin-889d@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/crm/{crm_id}/",
                wait_until="domcontentloaded",
            )
            # Page renders fine without the removed Slack-only empty panel.
            assert page.locator(
                '[data-testid="crm-slack-updates-empty"]'
            ).count() == 0
            assert page.locator(
                '[data-testid="crm-slack-thread"]'
            ).count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestMatchedUpdateLinkedToActivePlan:
    def test_thread_shows_on_plan_detail(
        self, django_server, django_db_blocker, browser, settings,
    ):
        for key, value in SLACK_SETTINGS.items():
            setattr(settings, key, value)

        with django_db_blocker.unblock():
            create_staff_user("admin-889e@test.com")
            user, record, plan = _make_member_with_plan(
                "thread-889e@test.com", "U_889G", active=True,
            )
            root = _msg("1700003000.000100", "U_889G", "Update tied to my plan")
            run = _run_ingest(_FakeSlackService(
                history=[root],
                replies={"1700003000.000100": [root]},
            ))
            assert run.members_matched == 1
            plan_id = plan.pk

        context = auth_context(browser, "admin-889e@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{plan_id}/",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="crm-slack-updates-section"]'
            ).count() == 1
            assert page.locator(
                '[data-testid="crm-slack-thread"]'
            ).count() == 1
            assert "Update tied to my plan" not in page.locator(
                '[data-testid="crm-slack-updates-section"]'
            ).inner_text()
            assert "Update tied to my plan" in page.locator(
                '[data-testid="member-notes-section"]'
            ).inner_text()
        finally:
            context.close()
