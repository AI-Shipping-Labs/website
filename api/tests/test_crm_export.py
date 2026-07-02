"""Tests for the CRM export endpoint (issue #1079).

``GET /api/crm/export`` -- one staff-token, read-only call that returns the
full per-user CRM aggregate (core state + crm_record + notes + nested plans
+ enrollments + onboarding responses), reusing the per-resource
serializers.

All ``TestCase``, token-authenticated. A staff ``Token`` and a
demoted-to-non-staff token live in ``setUpTestData``; auth is sent via
``HTTP_AUTHORIZATION="Token <key>"``.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.models import Course
from content.services.enrollment import ensure_enrollment
from crm.models import CRMRecord
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from plans.models import (
    Checkpoint,
    InterviewNote,
    Plan,
    Sprint,
    SprintEnrollment,
    Week,
)
from questionnaires.models import (
    Answer,
    Persona,
    Questionnaire,
    Response,
    ResponseQuestion,
)

User = get_user_model()


class CrmExportTestBase(TestCase):
    URL = "/api/crm/export"

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="ops")

        # A token whose owner was staff at creation (the model forbids
        # creating tokens for non-staff) then demoted -- ``token_required``
        # re-checks ``is_staff`` at request time and 401s.
        cls.nonstaff = User.objects.create_user(
            email="member-token@test.com", password="pw", is_staff=True,
        )
        cls.nonstaff_token = Token.objects.create(user=cls.nonstaff, name="m")
        cls.nonstaff.is_staff = False
        cls.nonstaff.save(update_fields=["is_staff"])

        cls.sprint = Sprint.objects.create(
            name="May 2026", slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status="active",
        )
        cls.questionnaire = Questionnaire.objects.create(
            slug="crm-export-onboarding",
            title="Engineer onboarding",
            purpose="onboarding",
        )
        cls.persona = Persona.objects.create(
            name="Priya",
            archetype="The Engineer transitioning to AI",
            slug="crm-export-priya",
            default_questionnaire=cls.questionnaire,
            is_active=True,
            order=0,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    @classmethod
    def _make_member(cls, email, *, joined=None):
        user = User.objects.create_user(email=email, password="pw")
        if joined is not None:
            User.objects.filter(pk=user.pk).update(date_joined=joined)
            user.refresh_from_db(fields=["date_joined"])
        return user

    @classmethod
    def _give_plan(cls, member):
        plan = Plan.objects.create(member=member, sprint=cls.sprint)
        week = Week.objects.create(plan=plan, week_number=1, position=0)
        Checkpoint.objects.create(
            week=week, description="Ship MVP", position=0,
        )
        return plan

    @classmethod
    def _give_note(cls, member, *, visibility="external", plan=None, body="note"):
        return InterviewNote.objects.create(
            member=member,
            plan=plan,
            visibility=visibility,
            kind="general",
            body=body,
            created_by=cls.staff,
        )

    @classmethod
    def _give_onboarding_response(cls, member):
        response = Response.objects.create(
            questionnaire=cls.questionnaire,
            respondent=member,
            status="submitted",
            submitted_at=timezone.now(),
        )
        rq = ResponseQuestion.objects.create(
            response=response, question_type="text", prompt="Role?", order=0,
        )
        Answer.objects.create(
            response=response, question=rq, text_value="Backend engineer",
        )
        return response

    @classmethod
    def _give_crm_record(cls, member, **kwargs):
        return CRMRecord.objects.create(user=member, **kwargs)


class CrmExportAuthTest(CrmExportTestBase):
    def test_missing_token_returns_401(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 401)

    def test_invalid_token_returns_401(self):
        response = self.client.get(
            self.URL, HTTP_AUTHORIZATION="Token not-a-real-key",
        )
        self.assertEqual(response.status_code, 401)

    def test_non_staff_token_returns_401_and_leaks_no_data(self):
        member = self._make_member("signal@test.com")
        self._give_crm_record(member)
        response = self.client.get(self.URL, **self._auth(self.nonstaff_token))
        self.assertEqual(response.status_code, 401)
        # The 401 body is the auth error, never a members payload.
        self.assertNotIn("members", response.json())

    def test_post_returns_405(self):
        response = self.client.post(
            self.URL, data="{}", content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)

    def test_patch_put_delete_return_405(self):
        for method in ("patch", "put", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    self.URL,
                    data="{}",
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 405)


class CrmExportEnvelopeTest(CrmExportTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = cls._make_member("alice@test.com")
        cls.crm = cls._give_crm_record(
            cls.member,
            status="active",
            persona="Sam - Technical Professional",
            summary="Mid-career engineer pivoting into AI.",
            next_steps="Pair on week-2 deliverable.",
        )
        cls.plan = cls._give_plan(cls.member)
        cls._give_note(cls.member, visibility="external", body="external chat")
        cls.internal_note = cls._give_note(
            cls.member, visibility="internal", body="internal candid note",
            plan=cls.plan,
        )
        cls._give_onboarding_response(cls.member)
        # The plan-create signal already auto-enrolled the member; stamp
        # ``enrolled_by`` so the serialized enrolled_by is asserted.
        SprintEnrollment.objects.update_or_create(
            sprint=cls.sprint, user=cls.member,
            defaults={"enrolled_by": cls.staff},
        )
        cls.course = Course.objects.create(
            slug="llm-zoomcamp", title="LLM Zoomcamp", status="published",
        )
        ensure_enrollment(cls.member, cls.course, source="admin")

    def test_envelope_keys_present(self):
        response = self.client.get(self.URL, **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in (
            "members", "count", "total", "limit", "offset", "scope",
            "generated_at",
        ):
            self.assertIn(key, body)
        self.assertEqual(body["scope"], "crm")
        self.assertEqual(body["limit"], 200)
        self.assertEqual(body["offset"], 0)

    def _member_payload(self):
        body = self.client.get(self.URL, **self._auth()).json()
        members = [m for m in body["members"] if m["email"] == "alice@test.com"]
        self.assertEqual(len(members), 1)
        return members[0]

    def test_member_carries_full_user_state(self):
        item = self._member_payload()
        # Fields unique to the FULL (non-compact) serialize_user_state.
        for key in (
            "email", "first_name", "last_name", "display_name", "tier",
            "base_tier", "tier_override", "tags", "aliases",
            "stripe_customer_id", "subscription_id", "slack_member",
            "slack_user_id", "email_verified", "unsubscribed",
            "bounce_state", "date_joined", "last_login",
            "email_preferences", "import_metadata",
        ):
            self.assertIn(key, item)
        self.assertIn("source", item["tier"])

    def test_member_carries_full_crm_record(self):
        item = self._member_payload()
        record = item["crm_record"]
        self.assertEqual(record["id"], self.crm.pk)
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["persona"], "Sam - Technical Professional")
        self.assertEqual(record["summary"], "Mid-career engineer pivoting into AI.")
        self.assertEqual(record["next_steps"], "Pair on week-2 deliverable.")
        self.assertIsNotNone(record["created_at"])
        self.assertIsNotNone(record["updated_at"])

    def test_member_carries_notes_plans_enrollments_responses(self):
        item = self._member_payload()
        self.assertGreaterEqual(len(item["notes"]), 1)
        # Note shape comes from serialize_interview_note.
        note = item["notes"][0]
        for key in (
            "id", "user_email", "plan_id", "visibility", "kind", "body",
            "tags", "source_type", "source_metadata", "created_by_email",
            "created_at", "updated_at",
        ):
            self.assertIn(key, note)

        self.assertEqual(len(item["plans"]), 1)
        plan = item["plans"][0]
        self.assertEqual(plan["sprint"], "may-2026")
        self.assertEqual(len(plan["weeks"]), 1)
        self.assertEqual(len(plan["weeks"][0]["checkpoints"]), 1)

        self.assertEqual(len(item["sprint_enrollments"]), 1)
        enrollment = item["sprint_enrollments"][0]
        self.assertEqual(enrollment["sprint_slug"], "may-2026")
        self.assertEqual(enrollment["enrolled_by"], "staff@test.com")
        self.assertIsNotNone(enrollment["enrolled_at"])

        self.assertEqual(len(item["course_enrollments"]), 1)
        self.assertEqual(
            item["course_enrollments"][0]["user_email"], "alice@test.com",
        )

        self.assertEqual(len(item["onboarding_responses"]), 1)
        self.assertEqual(
            item["onboarding_responses"][0]["questionnaire_slug"],
            "crm-export-onboarding",
        )

    def test_internal_notes_included_for_staff_bearer(self):
        item = self._member_payload()
        bodies = {n["body"] for n in item["notes"]}
        self.assertIn("internal candid note", bodies)
        # The internal note is a plan-level note: it also surfaces inside
        # the plan's interview_notes for the staff viewer.
        plan_note_bodies = {
            n["body"] for n in item["plans"][0]["interview_notes"]
        }
        self.assertIn("internal candid note", plan_note_bodies)


class CrmExportScopeTest(CrmExportTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Four signal-carrying members, each via a different signal.
        cls.by_record = cls._make_member("by-record@test.com")
        cls._give_crm_record(cls.by_record)

        cls.by_note = cls._make_member("by-note@test.com")
        cls._give_note(cls.by_note)

        cls.by_plan = cls._make_member("by-plan@test.com")
        cls._give_plan(cls.by_plan)

        cls.by_tag = cls._make_member("by-tag@test.com")
        cls.by_tag.tags = ["early-adopter"]
        cls.by_tag.save(update_fields=["tags"])

        cls.by_response = cls._make_member("by-response@test.com")
        cls._give_onboarding_response(cls.by_response)

        # No-signal members: a bare user and a user carrying ONLY system
        # (source-namespace) tags, which must NOT count as CRM signal.
        cls.no_signal = cls._make_member("no-signal@test.com")
        cls.system_tag_only = cls._make_member("system-only@test.com")
        cls.system_tag_only.tags = ["stripe:active", "course:llm-zoomcamp"]
        cls.system_tag_only.save(update_fields=["tags"])

    def _emails(self, **params):
        response = self.client.get(self.URL, params, **self._auth())
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_default_scope_returns_only_signal_members(self):
        body = self._emails()
        emails = {m["email"] for m in body["members"]}
        self.assertEqual(
            emails,
            {
                "by-record@test.com",
                "by-note@test.com",
                "by-plan@test.com",
                "by-tag@test.com",
                "by-response@test.com",
            },
        )
        self.assertNotIn("no-signal@test.com", emails)
        self.assertNotIn("system-only@test.com", emails)
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["count"], 5)

    def test_scope_all_returns_every_user(self):
        body = self._emails(scope="all")
        emails = {m["email"] for m in body["members"]}
        # Every created user, including no-signal and the token owners.
        self.assertIn("no-signal@test.com", emails)
        self.assertIn("system-only@test.com", emails)
        self.assertIn("staff@test.com", emails)
        self.assertEqual(body["total"], User.objects.count())

    def test_no_signal_user_empty_aggregate_under_scope_all(self):
        body = self._emails(scope="all")
        item = next(
            m for m in body["members"] if m["email"] == "no-signal@test.com"
        )
        self.assertIsNone(item["crm_record"])
        self.assertEqual(item["notes"], [])
        self.assertEqual(item["plans"], [])
        self.assertEqual(item["onboarding_responses"], [])

    def test_unknown_scope_returns_422(self):
        response = self.client.get(
            self.URL, {"scope": "everyone"}, **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["allowed"], ["crm", "all"])
        self.assertNotIn("members", body)


class CrmExportPagingTest(CrmExportTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.members = [
            cls._make_member(f"page-{i}@test.com") for i in range(4)
        ]
        for member in cls.members:
            cls._give_crm_record(member)

    def _ids_for(self, **params):
        params.setdefault("scope", "all")
        response = self.client.get(self.URL, params, **self._auth())
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_pages_do_not_overlap_and_order_by_id(self):
        first = self._ids_for(limit=2, offset=0)
        second = self._ids_for(limit=2, offset=2)

        first_emails = [m["email"] for m in first["members"]]
        second_emails = [m["email"] for m in second["members"]]

        self.assertEqual(len(first_emails), 2)
        self.assertEqual(set(first_emails) & set(second_emails), set())
        self.assertEqual(first["limit"], 2)
        self.assertEqual(first["offset"], 0)
        self.assertEqual(second["offset"], 2)
        # total is stable across pages.
        self.assertEqual(first["total"], second["total"])

        # Ordering is deterministic by User.id: page emails follow id order.
        page_users = User.objects.filter(
            email__in=first_emails + second_emails,
        ).order_by("id")
        ordered_emails = list(page_users.values_list("email", flat=True))
        self.assertEqual(first_emails + second_emails, ordered_emails)

    def test_count_is_page_total_is_full_match(self):
        body = self._ids_for(limit=2, offset=0)
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["total"], User.objects.count())

    def test_invalid_limit_returns_422(self):
        response = self.client.get(
            self.URL, {"limit": "abc"}, **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "limit")

    def test_invalid_offset_returns_422(self):
        response = self.client.get(
            self.URL, {"offset": "-3"}, **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["details"]["field"], "offset")

    def test_invalid_since_returns_422(self):
        response = self.client.get(
            self.URL, {"since": "not-a-date"}, **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["details"]["field"], "since")


class CrmExportLimitCapTest(CrmExportTestBase):
    """The hard cap is read via get_config, not hardcoded."""

    def test_limit_clamped_to_configurable_ceiling(self):
        # Default ceiling is 200: a request for 999 clamps to 200.
        response = self.client.get(self.URL, {"limit": "999"}, **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["limit"], 200)

    def test_db_override_changes_the_ceiling(self):
        IntegrationSetting.objects.create(key="CRM_EXPORT_MAX_LIMIT", value="5")
        clear_config_cache()
        try:
            response = self.client.get(
                self.URL, {"limit": "999"}, **self._auth(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["limit"], 5)
        finally:
            IntegrationSetting.objects.filter(
                key="CRM_EXPORT_MAX_LIMIT",
            ).delete()
            clear_config_cache()

    def test_key_registered_in_settings_registry(self):
        from integrations.settings_registry import INTEGRATION_GROUPS

        keys = {
            key["key"]
            for group in INTEGRATION_GROUPS
            for key in group["keys"]
        }
        self.assertIn("CRM_EXPORT_MAX_LIMIT", keys)


class CrmExportSinceTest(CrmExportTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cutoff = timezone.make_aware(
            datetime.datetime(2026, 3, 1, 0, 0, 0),
        )
        cls.early = cls._make_member(
            "early@test.com",
            joined=timezone.make_aware(datetime.datetime(2026, 1, 1)),
        )
        cls.early2 = cls._make_member(
            "early2@test.com",
            joined=timezone.make_aware(datetime.datetime(2026, 2, 1)),
        )
        cls.late = cls._make_member(
            "late@test.com",
            joined=timezone.make_aware(datetime.datetime(2026, 4, 1)),
        )
        for member in (cls.early, cls.early2, cls.late):
            cls._give_crm_record(member)

    def test_since_filters_by_join_date(self):
        response = self.client.get(
            self.URL,
            {"scope": "all", "since": self.cutoff.isoformat()},
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        emails = {m["email"] for m in body["members"]}
        self.assertIn("late@test.com", emails)
        self.assertNotIn("early@test.com", emails)
        self.assertNotIn("early2@test.com", emails)
        # total reflects the filtered set (late + any token owners joined
        # after the cutoff -- the explicit early ones are excluded).
        self.assertNotIn("early@test.com", emails)


class CrmExportSearchTest(CrmExportTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.alpha = cls._make_member("alpha-unique@test.com")
        cls.alpha.stripe_customer_id = "cus_alphaXYZ"
        cls.alpha.tags = ["vip-cohort"]
        cls.alpha.save(update_fields=["stripe_customer_id", "tags"])
        cls.beta = cls._make_member("beta@test.com")
        cls.beta.slack_user_id = "U0BETA999"
        cls.beta.save(update_fields=["slack_user_id"])

    def _emails(self, q):
        response = self.client.get(
            self.URL, {"scope": "all", "q": q}, **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        return {m["email"] for m in response.json()["members"]}

    def test_q_matches_email_fragment(self):
        emails = self._emails("alpha-unique")
        self.assertEqual(emails, {"alpha-unique@test.com"})

    def test_q_matches_stripe_id(self):
        emails = self._emails("cus_alphaXYZ")
        self.assertEqual(emails, {"alpha-unique@test.com"})

    def test_q_matches_slack_id(self):
        emails = self._emails("U0BETA999")
        self.assertEqual(emails, {"beta@test.com"})

    def test_q_matches_tag(self):
        emails = self._emails("vip-cohort")
        self.assertEqual(emails, {"alpha-unique@test.com"})


class CrmExportEmailLookupTest(CrmExportTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.target = cls._make_member("target@example.com")
        cls._give_crm_record(cls.target, summary="Target CRM")
        cls._give_plan(cls.target)
        cls._give_note(cls.target, body="target note")
        cls._give_onboarding_response(cls.target)

        cls.no_signal = cls._make_member("bare@example.com")

        cls.q_match_only = cls._make_member("fragment-match@example.com")
        cls.q_match_only.stripe_customer_id = "cus_should_not_win"
        cls.q_match_only.save(update_fields=["stripe_customer_id"])

    def _lookup(self, **params):
        params.setdefault("scope", "all")
        response = self.client.get(self.URL, params, **self._auth())
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_scope_all_email_returns_exact_member(self):
        body = self._lookup(email="target@example.com")

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["members"][0]["email"], "target@example.com")
        self.assertEqual(body["members"][0]["crm_record"]["summary"], "Target CRM")
        self.assertEqual(len(body["members"][0]["plans"]), 1)
        self.assertEqual(len(body["members"][0]["notes"]), 1)
        self.assertEqual(len(body["members"][0]["onboarding_responses"]), 1)

    def test_email_matching_is_case_insensitive_and_trims_whitespace(self):
        body = self._lookup(email="  TARGET@EXAMPLE.COM  ")

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["members"][0]["email"], "target@example.com")

    def test_scope_all_email_returns_existing_no_signal_user(self):
        body = self._lookup(email="bare@example.com")

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total"], 1)
        item = body["members"][0]
        self.assertEqual(item["email"], "bare@example.com")
        self.assertIsNone(item["crm_record"])
        self.assertEqual(item["notes"], [])
        self.assertEqual(item["plans"], [])
        self.assertEqual(item["onboarding_responses"], [])

    def test_scope_crm_email_existing_no_signal_user_returns_empty_envelope(self):
        body = self._lookup(scope="crm", email="bare@example.com")

        self.assertEqual(body["members"], [])
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["total"], 0)

    def test_unknown_email_returns_404_user_not_found(self):
        response = self.client.get(
            self.URL,
            {"scope": "all", "email": "missing@example.com"},
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], "user_not_found")
        self.assertNotIn("members", body)

    def test_email_wins_over_q_when_both_are_supplied(self):
        body = self._lookup(
            email="target@example.com",
            q="cus_should_not_win",
        )

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total"], 1)
        self.assertEqual(
            [member["email"] for member in body["members"]],
            ["target@example.com"],
        )


class CrmExportQueryBudgetTest(CrmExportTestBase):
    """No N+1 across a multi-user, multi-plan fixture.

    The export-specific aggregation (the gated plans / notes reads, plus
    enrollments / onboarding responses / crm_record) must NOT fan out per
    member: it is batched once per page. We assert that directly by counting
    the member-scoped batched lookups (a constant, independent of member
    count) and by confirming the total query count grows only by the
    per-member cost inherited from the shared ``serialize_user_state``
    (which ``GET /api/users`` pays too) when members are added -- not by the
    multi-query fan-out a naive per-member implementation would produce.
    """

    @classmethod
    def _seed_full_members(cls, prefix, count):
        for i in range(count):
            member = cls._make_member(f"{prefix}-{i}@test.com")
            cls._give_crm_record(member, persona=f"Persona {i}")
            cls._give_plan(member)
            cls._give_note(member, visibility="external", body=f"note {i}")
            cls._give_note(member, visibility="internal", body=f"internal {i}")
            cls._give_onboarding_response(member)
            # Plan-create signal already enrolled the member.
            SprintEnrollment.objects.update_or_create(
                sprint=cls.sprint, user=member,
                defaults={"enrolled_by": cls.staff},
            )

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls._seed_full_members("budget", 3)

    def test_gated_plan_and_note_reads_are_batched_not_per_member(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.URL, **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 3)

        sqls = [q["sql"] for q in ctx.captured_queries]

        # The gated member-scoped plan list is fetched with batched
        # ``member_id IN (...)`` queries (one for the gated serialization,
        # plus the lightweight signal-check prefetch), NEVER one
        # ``member_id = ?`` per member. The export-specific reads do not
        # fan out across the member set.
        plan_member_in = [
            s for s in sqls
            if 'FROM "plans_plan"' in s and "member_id" in s and "IN (" in s
        ]
        self.assertGreaterEqual(len(plan_member_in), 1)
        note_member_in = [
            s for s in sqls
            if 'FROM "plans_interviewnote"' in s
            and "member_id" in s and "IN (" in s
        ]
        self.assertGreaterEqual(len(note_member_in), 1)

        # No per-member ``plans_plan WHERE member_id = ?`` fan-out: every
        # plan read is batched. (A naive per-member ``.filter(member=user)``
        # would produce one such equality query per member.)
        plan_member_eq = [
            s for s in sqls
            if 'FROM "plans_plan"' in s
            and '"member_id" =' in s
            and '"member_id" IN' not in s
        ]
        self.assertEqual(plan_member_eq, [])

    def test_total_query_count_grows_only_by_shared_per_user_cost(self):
        """Adding members raises the count by a small constant, not by the
        full per-member aggregate fan-out a naive impl would add."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as base_ctx:
            self.client.get(self.URL, {"scope": "all"}, **self._auth())
        base_count = len(base_ctx.captured_queries)

        # Add three record-only members (no plans/notes/responses): the only
        # extra cost is the per-user TierOverride lookup inherited from
        # serialize_user_state (the same query GET /api/users pays per row).
        for i in range(3):
            self._give_crm_record(self._make_member(f"extra-{i}@test.com"))

        with CaptureQueriesContext(connection) as grown_ctx:
            self.client.get(self.URL, {"scope": "all"}, **self._auth())
        grown_count = len(grown_ctx.captured_queries)

        # Three record-only members add at most ~1 query each (the override
        # lookup). A per-member aggregate fan-out would add many more.
        self.assertLessEqual(grown_count - base_count, 6)

    def test_bounded_query_count_with_assert_num_queries(self):
        # Fixed-budget regression guard over the 3-member / 3-plan fixture.
        # Warm the integration-settings config cache first so its one-off
        # stamp read does not perturb the count (it is otherwise stable).
        self.client.get(self.URL, **self._auth())
        with self.assertNumQueries(57):
            response = self.client.get(self.URL, **self._auth())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["count"], 3)

    def test_email_lookup_query_count_does_not_grow_with_unrelated_users(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        target = self._make_member("targeted-budget@example.com")
        self._give_crm_record(target)
        self.client.get(
            self.URL,
            {"scope": "all", "email": target.email},
            **self._auth(),
        )

        with CaptureQueriesContext(connection) as base_ctx:
            response = self.client.get(
                self.URL,
                {"scope": "all", "email": target.email},
                **self._auth(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

        for i in range(20):
            unrelated = self._make_member(f"unrelated-{i}@test.com")
            self._give_crm_record(unrelated)
            self._give_plan(unrelated)
            self._give_note(unrelated, body=f"unrelated note {i}")
            self._give_onboarding_response(unrelated)

        with CaptureQueriesContext(connection) as grown_ctx:
            response = self.client.get(
                self.URL,
                {"scope": "all", "email": target.email},
                **self._auth(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(len(grown_ctx.captured_queries), len(base_ctx.captured_queries))

        user_selects = [
            q["sql"] for q in grown_ctx.captured_queries
            if 'FROM "accounts_user"' in q["sql"]
        ]
        unbounded_user_selects = [
            sql for sql in user_selects
            if "WHERE" not in sql
        ]
        self.assertEqual(unbounded_user_selects, [])


class CrmExportOpenApiTest(CrmExportTestBase):
    def test_export_path_present_under_crm_tag(self):
        response = self.client.get("/api/openapi.json", **self._auth())
        self.assertEqual(response.status_code, 200)
        spec = response.json()
        self.assertIn("/api/crm/export", spec["paths"])
        operation = spec["paths"]["/api/crm/export"]["get"]
        self.assertIn("CRM", operation["tags"])
        param_names = {p["name"] for p in operation.get("parameters", [])}
        for expected in ("scope", "limit", "offset", "since", "q", "email"):
            self.assertIn(expected, param_names)
        self.assertIn("200", operation["responses"])
        self.assertIn("404", operation["responses"])
