"""Tests for ``/api/sprints/<slug>/progress-evidence`` (issue #1048)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from crm.models import (
    AppliedProgressChange,
    IngestedProgressEvent,
    SlackMessage,
    SlackThread,
)
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Sprint,
    SprintEnrollment,
    Week,
)

User = get_user_model()


class SprintProgressEvidenceBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="ops")
        cls.source = Sprint.objects.create(
            name="May 2026",
            slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
            status="active",
        )
        cls.target = Sprint.objects.create(
            name="June 2026",
            slug="june-2026",
            start_date=datetime.date(2026, 6, 15),
            duration_weeks=6,
            status="draft",
        )

    def _auth(self, token=None):
        token = token or self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _url(self, source_slug="may-2026", query=""):
        return f"/api/sprints/{source_slug}/progress-evidence{query}"

    def _member(self, email, **kwargs):
        return User.objects.create_user(email=email, password="pw", **kwargs)

    def _enroll(self, member, sprint=None):
        return SprintEnrollment.objects.create(
            sprint=sprint or self.source,
            user=member,
        )

    def _plan(self, member, sprint=None, goal="Ship progress"):
        return Plan.objects.create(
            member=member,
            sprint=sprint or self.source,
            goal=goal,
        )

    def _thread(self, plan, *, text="I shipped the demo", ts="1770000000.000100"):
        posted_at = timezone.now()
        thread = SlackThread.objects.create(
            channel_id="C_PLAN_SPRINTS",
            thread_ts=ts,
            slack_user_id="U123",
            member=plan.member,
            plan=plan,
            posted_at=posted_at,
            permalink=f"https://slack.example/archives/C_PLAN_SPRINTS/p{ts}",
            reply_count=1,
        )
        SlackMessage.objects.create(
            thread=thread,
            ts=ts,
            slack_user_id="U123",
            author_display="Member",
            text=text,
            posted_at=posted_at,
            is_root=True,
        )
        SlackMessage.objects.create(
            thread=thread,
            ts="1770000001.000200",
            slack_user_id="U999",
            author_display="Coach",
            text="Great update",
            posted_at=posted_at + datetime.timedelta(minutes=1),
            is_root=False,
        )
        return thread


class SprintProgressEvidenceAuthTest(SprintProgressEvidenceBase):
    def test_requires_staff_token_not_session_or_nonstaff_token(self):
        member = self._member("member@test.com")
        self._enroll(member)
        nonstaff_token = Token(
            key="nonstaff-token",
            user=member,
            name="bad",
        )
        Token.objects.bulk_create([nonstaff_token])

        anonymous = self.client.get(self._url())
        invalid = self.client.get(
            self._url(),
            HTTP_AUTHORIZATION="Token does-not-exist",
        )
        nonstaff = self.client.get(self._url(), **self._auth(nonstaff_token))

        self.client.force_login(self.staff)
        session_only = self.client.get(self._url())

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(nonstaff.status_code, 401)
        self.assertEqual(session_only.status_code, 401)

    def test_unsupported_method_returns_existing_405_shape(self):
        response = self.client.post(self._url(), **self._auth())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": "Method not allowed"})


class SprintProgressEvidenceReportTest(SprintProgressEvidenceBase):
    def test_empty_sprint_returns_zero_totals(self):
        response = self.client.get(self._url(), **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_sprint"]["slug"], "may-2026")
        self.assertIsNone(body["target_sprint"])
        self.assertEqual(body["members"], [])
        self.assertEqual(
            body["totals"],
            {
                "members": 0,
                "app_progress": 0,
                "crm_update_progress": 0,
                "both": 0,
                "none": 0,
                "target_enrolled": 0,
                "target_plan_exists": 0,
            },
        )

    def test_reports_app_crm_both_none_and_orders_members_by_email(self):
        now = timezone.now()
        app_member = self._member("app@test.com", first_name="App")
        both_member = self._member("both@test.com", first_name="Both")
        crm_member = self._member("crm@test.com", first_name="Crm")
        no_plan_member = self._member("none@test.com", first_name="None")
        for member in [crm_member, no_plan_member, both_member, app_member]:
            self._enroll(member)

        app_plan = self._plan(app_member, goal="App goal")
        app_week = Week.objects.create(plan=app_plan, week_number=1)
        checkpoint = Checkpoint.objects.create(
            week=app_week,
            description="Finish a working prototype",
            done_at=now,
        )
        Deliverable.objects.create(
            plan=app_plan,
            description="Unfinished deliverable",
            done_at=None,
        )

        crm_plan = self._plan(crm_member, goal="CRM goal")
        crm_thread = self._thread(
            crm_plan,
            text="I finished the Slack-driven update",
            ts="1770000002.000100",
        )

        both_plan = self._plan(both_member, goal="Both goal")
        deliverable = Deliverable.objects.create(
            plan=both_plan,
            description="Publish the case study",
            done_at=now + datetime.timedelta(hours=1),
        )
        NextStep.objects.create(
            plan=both_plan,
            description="Unfinished next step",
        )
        both_thread = self._thread(
            both_plan,
            text="Done with the case study",
            ts="1770000003.000100",
        )
        event = IngestedProgressEvent.objects.create(
            thread=both_thread,
            plan=both_plan,
            summary="Member completed the case study.",
            blockers=["Waiting for review"],
            model_name="test-parser",
            source_message_ts="1770000003.000100",
        )
        AppliedProgressChange.objects.create(
            event=event,
            item_kind="deliverable",
            deliverable=deliverable,
        )

        response = self.client.get(self._url(), **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            [row["member"]["email"] for row in body["members"]],
            ["app@test.com", "both@test.com", "crm@test.com", "none@test.com"],
        )
        self.assertEqual(
            body["totals"],
            {
                "members": 4,
                "app_progress": 1,
                "crm_update_progress": 1,
                "both": 1,
                "none": 1,
                "target_enrolled": 0,
                "target_plan_exists": 0,
            },
        )

        rows = {row["member"]["email"]: row for row in body["members"]}
        app_row = rows["app@test.com"]
        self.assertEqual(app_row["evidence_status"], "app_progress")
        self.assertEqual(app_row["evidence_reasons"], ["app_progress"])
        self.assertEqual(app_row["source_plan"]["goal"], "App goal")
        self.assertEqual(app_row["app_progress"]["total_done"], 1)
        self.assertEqual(app_row["app_progress"]["checkpoints_done"], 1)
        self.assertEqual(app_row["app_progress"]["deliverables_done"], 0)
        self.assertEqual(app_row["app_progress"]["next_steps_done"], 0)
        self.assertEqual(
            app_row["app_progress"]["evidence"][0],
            {
                "kind": "checkpoint",
                "id": checkpoint.id,
                "done_at": checkpoint.done_at.isoformat(),
                "description": "Finish a working prototype",
            },
        )

        crm_row = rows["crm@test.com"]
        self.assertEqual(crm_row["evidence_status"], "crm_update_progress")
        self.assertEqual(crm_row["app_progress"]["total_done"], 0)
        self.assertEqual(crm_row["crm_progress"]["threads_count"], 1)
        self.assertEqual(crm_row["crm_progress"]["parsed_events_count"], 0)
        crm_thread_row = crm_row["crm_progress"]["threads"][0]
        self.assertEqual(crm_thread_row["id"], crm_thread.id)
        self.assertEqual(crm_thread_row["permalink"], crm_thread.permalink)
        self.assertIn("Slack-driven update", crm_thread_row["root_message"])
        self.assertEqual(len(crm_thread_row["messages"]), 2)
        self.assertIsNone(crm_thread_row["progress_event"])

        both_row = rows["both@test.com"]
        self.assertEqual(both_row["evidence_status"], "both")
        self.assertEqual(
            both_row["evidence_reasons"],
            ["app_progress", "crm_update_progress"],
        )
        self.assertEqual(both_row["app_progress"]["total_done"], 1)
        progress_event = both_row["crm_progress"]["threads"][0]["progress_event"]
        self.assertEqual(progress_event["summary"], "Member completed the case study.")
        self.assertEqual(progress_event["blockers"], ["Waiting for review"])
        self.assertEqual(progress_event["model_name"], "test-parser")
        self.assertEqual(progress_event["source_message_ts"], "1770000003.000100")
        self.assertEqual(progress_event["changes"][0]["item_kind"], "deliverable")
        self.assertEqual(progress_event["changes"][0]["item_id"], deliverable.id)
        self.assertEqual(
            progress_event["changes"][0]["item_description"],
            "Publish the case study",
        )

        none_row = rows["none@test.com"]
        self.assertIsNone(none_row["source_plan"])
        self.assertEqual(none_row["evidence_status"], "none")
        self.assertEqual(none_row["evidence_reasons"], [])
        self.assertEqual(none_row["app_progress"]["total_done"], 0)
        self.assertEqual(none_row["crm_progress"]["threads"], [])

    def test_excludes_crm_threads_linked_to_other_sprint_plan(self):
        member = self._member("member@test.com")
        self._enroll(member)
        source_plan = self._plan(member)
        other_sprint = Sprint.objects.create(
            name="Other",
            slug="other",
            start_date=datetime.date(2026, 7, 1),
            duration_weeks=6,
        )
        other_plan = self._plan(member, sprint=other_sprint)
        self._thread(other_plan, text="Progress in a different sprint")

        response = self.client.get(self._url(), **self._auth())
        self.assertEqual(response.status_code, 200)
        row = response.json()["members"][0]
        self.assertEqual(row["source_plan"]["id"], source_plan.id)
        self.assertEqual(row["evidence_status"], "none")
        self.assertEqual(row["crm_progress"]["threads_count"], 0)

    def test_target_sprint_annotations(self):
        enrolled = self._member("enrolled@test.com")
        planned = self._member("planned@test.com")
        self._enroll(enrolled)
        self._enroll(planned)
        target_enrollment = self._enroll(enrolled, sprint=self.target)
        Plan.objects.bulk_create([
            Plan(member=planned, sprint=self.target, goal="Target plan"),
        ])
        target_plan = Plan.objects.get(member=planned, sprint=self.target)

        response = self.client.get(
            self._url(query="?target_sprint=june-2026"),
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["target_sprint"]["slug"], "june-2026")
        self.assertEqual(body["totals"]["target_enrolled"], 1)
        self.assertEqual(body["totals"]["target_plan_exists"], 1)
        rows = {row["member"]["email"]: row for row in body["members"]}
        self.assertEqual(
            rows["enrolled@test.com"]["target"],
            {
                "sprint_slug": "june-2026",
                "enrollment_id": target_enrollment.id,
                "enrolled": True,
                "plan_id": None,
                "plan_exists": False,
            },
        )
        self.assertEqual(rows["planned@test.com"]["target"]["plan_id"], target_plan.id)
        self.assertTrue(rows["planned@test.com"]["target"]["plan_exists"])

    def test_unknown_source_and_target_have_stable_error_codes(self):
        unknown_source = self.client.get(
            self._url(source_slug="unknown"),
            **self._auth(),
        )
        self.assertEqual(unknown_source.status_code, 404)
        self.assertEqual(unknown_source.json()["code"], "unknown_sprint")

        unknown_target = self.client.get(
            self._url(query="?target_sprint=unknown"),
            **self._auth(),
        )
        self.assertEqual(unknown_target.status_code, 422)
        self.assertEqual(
            unknown_target.json()["code"],
            "unknown_target_sprint",
        )

    def test_endpoint_is_read_only_for_progress_and_ingest_models(self):
        member = self._member("readonly@test.com")
        self._enroll(member)
        plan = self._plan(member)
        week = Week.objects.create(plan=plan, week_number=1)
        checkpoint = Checkpoint.objects.create(
            week=week,
            description="Already done",
            done_at=timezone.now(),
        )
        next_step = NextStep.objects.create(
            plan=plan,
            description="Still open",
            done_at=None,
        )
        thread = self._thread(plan)
        event = IngestedProgressEvent.objects.create(
            thread=thread,
            plan=plan,
            summary="Readonly",
        )
        AppliedProgressChange.objects.create(
            event=event,
            item_kind="checkpoint",
            checkpoint=checkpoint,
        )
        counts_before = {
            "plans": Plan.objects.count(),
            "enrollments": SprintEnrollment.objects.count(),
            "threads": SlackThread.objects.count(),
            "messages": SlackMessage.objects.count(),
            "events": IngestedProgressEvent.objects.count(),
            "changes": AppliedProgressChange.objects.count(),
        }
        done_before = {
            "checkpoint": checkpoint.done_at,
            "next_step": next_step.done_at,
        }

        response = self.client.get(self._url(), **self._auth())

        checkpoint.refresh_from_db()
        next_step.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(counts_before["plans"], Plan.objects.count())
        self.assertEqual(
            counts_before["enrollments"],
            SprintEnrollment.objects.count(),
        )
        self.assertEqual(counts_before["threads"], SlackThread.objects.count())
        self.assertEqual(counts_before["messages"], SlackMessage.objects.count())
        self.assertEqual(
            counts_before["events"],
            IngestedProgressEvent.objects.count(),
        )
        self.assertEqual(
            counts_before["changes"],
            AppliedProgressChange.objects.count(),
        )
        self.assertEqual(checkpoint.done_at, done_before["checkpoint"])
        self.assertEqual(next_step.done_at, done_before["next_step"])
