"""Playwright API coverage for sprint progress evidence (issue #1048)."""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Token
from crm.models import SlackMessage, SlackThread
from plans.models import Checkpoint, Deliverable, Plan, Sprint, SprintEnrollment, Week

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

User = get_user_model()


def _seed_progress_evidence_fixture():
    staff = User.objects.create_user(
        email="pw-progress-staff@test.com",
        password="pw",
        is_staff=True,
    )
    token = Token.objects.create(user=staff, name="playwright-progress")
    source = Sprint.objects.create(
        name="May 2026",
        slug="pw-may-2026",
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=6,
        status="active",
    )

    app_member = User.objects.create_user(email="pw-app@test.com", password="pw")
    crm_member = User.objects.create_user(email="pw-crm@test.com", password="pw")
    both_member = User.objects.create_user(email="pw-both@test.com", password="pw")
    none_member = User.objects.create_user(email="pw-none@test.com", password="pw")
    for member in [app_member, crm_member, both_member, none_member]:
        SprintEnrollment.objects.create(sprint=source, user=member)

    now = timezone.now()
    app_plan = Plan.objects.create(member=app_member, sprint=source, goal="App")
    app_week = Week.objects.create(plan=app_plan, week_number=1)
    Checkpoint.objects.create(
        week=app_week,
        description="Ship app progress",
        done_at=now,
    )

    crm_plan = Plan.objects.create(member=crm_member, sprint=source, goal="CRM")
    _create_thread(crm_plan, "1770000100.000100", "I shipped from Slack")

    both_plan = Plan.objects.create(member=both_member, sprint=source, goal="Both")
    Deliverable.objects.create(
        plan=both_plan,
        description="Ship both progress",
        done_at=now + datetime.timedelta(minutes=5),
    )
    _create_thread(both_plan, "1770000200.000100", "Slack plus app progress")

    return token.key, source.slug


def _create_thread(plan, ts, text):
    posted_at = timezone.now()
    thread = SlackThread.objects.create(
        channel_id="C_PLAN_SPRINTS",
        thread_ts=ts,
        member=plan.member,
        plan=plan,
        posted_at=posted_at,
        permalink=f"https://slack.example/archives/C_PLAN_SPRINTS/p{ts}",
    )
    SlackMessage.objects.create(
        thread=thread,
        ts=ts,
        author_display="Member",
        text=text,
        posted_at=posted_at,
        is_root=True,
    )


def test_staff_operator_classifies_next_sprint_candidates(
    django_server,
    django_db_blocker,
    browser,
):
    with django_db_blocker.unblock():
        token_key, source_slug = _seed_progress_evidence_fixture()

    context = browser.new_context()
    response = context.request.get(
        f"{django_server}/api/sprints/{source_slug}/progress-evidence",
        headers={"Authorization": f"Token {token_key}"},
    )
    assert response.status == 200
    body = response.json()
    rows = {
        row["member"]["email"]: row["evidence_status"]
        for row in body["members"]
    }

    include = {
        email for email, status in rows.items()
        if status in {"app_progress", "crm_update_progress", "both"}
    }
    exclude = {email for email, status in rows.items() if status == "none"}

    assert include == {
        "pw-app@test.com",
        "pw-crm@test.com",
        "pw-both@test.com",
    }
    assert exclude == {"pw-none@test.com"}
    context.close()
