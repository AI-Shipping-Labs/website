"""Tests for the read-only onboarding API (issue #837).

All ``TestCase``, token-authenticated. A staff ``Token`` lives in
``setUpTestData`` and is sent via ``HTTP_AUTHORIZATION="Token <key>"``.
Covers every scenario in the issue: per-member ordered Q&A with each
answer type, unanswered handling, persona resolution (specific + generic
-> null), bulk since/status/persona/pagination/total, snapshot fidelity
after a base-question edit, the survey-definition endpoints, the two
distinct 404s, auth (401 missing/non-staff), and 405 non-GET.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from questionnaires.models import (
    Answer,
    Persona,
    Question,
    Questionnaire,
    QuestionOption,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
)

User = get_user_model()


class _OnboardingApiBase(TestCase):
    """Shared staff token + helpers for building snapshot responses."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="ops")
        # A token whose owner is staff at creation (the model forbids
        # creating tokens for non-staff) then demoted -- ``token_required``
        # re-checks ``is_staff`` at request time and 401s.
        cls.nonstaff = User.objects.create_user(
            email="member@test.com", password="pw", is_staff=True,
        )
        cls.nonstaff_token = Token.objects.create(user=cls.nonstaff, name="m")
        cls.nonstaff.is_staff = False
        cls.nonstaff.save(update_fields=["is_staff"])

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    @staticmethod
    def _make_questionnaire(slug, purpose="onboarding", **kwargs):
        return Questionnaire.objects.create(
            slug=slug,
            title=kwargs.pop("title", slug.replace("-", " ").title()),
            purpose=purpose,
            **kwargs,
        )

    @staticmethod
    def _add_response_question(response, *, qtype, prompt, order, source=None):
        return ResponseQuestion.objects.create(
            response=response,
            source_question=source,
            question_type=qtype,
            prompt=prompt,
            order=order,
        )

    @staticmethod
    def _add_rq_option(rq, label, order, source=None):
        return ResponseQuestionOption.objects.create(
            response_question=rq,
            source_option=source,
            label=label,
            order=order,
        )


class PerMemberResponseTest(_OnboardingApiBase):
    """B1: ordered Q&A feed with every answer type + unanswered handling."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.q = cls._make_questionnaire("t-engineer")
        cls.persona = Persona.objects.create(
            name="Priya",
            archetype="The Engineer transitioning to AI",
            slug="t-priya",
            default_questionnaire=cls.q,
            is_active=True,
            order=0,
        )
        cls.member = User.objects.create_user(email="alex@test.com", password="pw")
        cls.response = Response.objects.create(
            questionnaire=cls.q,
            respondent=cls.member,
            status="submitted",
            submitted_at=timezone.now(),
        )

        rq_text = cls._add_response_question(
            cls.response, qtype="text", prompt="Role?", order=0,
        )
        Answer.objects.create(
            response=cls.response, question=rq_text, text_value="Backend engineer",
        )

        rq_scale = cls._add_response_question(
            cls.response, qtype="scale", prompt="Confidence 1-5?", order=1,
        )
        Answer.objects.create(
            response=cls.response, question=rq_scale, number_value=3,
        )

        rq_single = cls._add_response_question(
            cls.response, qtype="single_choice", prompt="Primary stack?", order=2,
        )
        opt_a = cls._add_rq_option(rq_single, "Python", 0)
        cls._add_rq_option(rq_single, "Go", 1)
        ans_single = Answer.objects.create(
            response=cls.response, question=rq_single,
        )
        ans_single.selected_options.set([opt_a])

        rq_multi = cls._add_response_question(
            cls.response, qtype="multiple_choice", prompt="Interests?", order=3,
        )
        m_llm = cls._add_rq_option(rq_multi, "LLM apps", 0)
        m_mlops = cls._add_rq_option(rq_multi, "MLOps", 1)
        ans_multi = Answer.objects.create(
            response=cls.response, question=rq_multi,
        )
        ans_multi.selected_options.set([m_llm, m_mlops])

        # Unanswered long_text and multiple_choice (no Answer rows).
        cls._add_response_question(
            cls.response, qtype="long_text", prompt="Anything else?", order=4,
        )
        rq_multi_empty = cls._add_response_question(
            cls.response, qtype="multiple_choice", prompt="Tools?", order=5,
        )
        cls._add_rq_option(rq_multi_empty, "Docker", 0)

    def test_every_answer_type_and_ordering(self):
        resp = self.client.get(
            f"/api/onboarding/responses/{self.member.email}", **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["email"], "alex@test.com")
        self.assertEqual(data["questionnaire_slug"], "t-engineer")
        self.assertEqual(data["status"], "submitted")
        self.assertIsNotNone(data["submitted_at"])

        orders = [q["order"] for q in data["questions"]]
        self.assertEqual(orders, sorted(orders))

        by_order = {q["order"]: q for q in data["questions"]}
        self.assertEqual(by_order[0]["answer"], "Backend engineer")
        self.assertEqual(by_order[1]["answer"], 3)
        self.assertEqual(by_order[2]["answer"], "Python")
        self.assertEqual(by_order[3]["answer"], ["LLM apps", "MLOps"])

    def test_unanswered_questions_included_with_empty_values(self):
        resp = self.client.get(
            f"/api/onboarding/responses/{self.member.email}", **self._auth(),
        )
        by_order = {q["order"]: q for q in resp.json()["questions"]}
        # long_text with no Answer -> null
        self.assertEqual(by_order[4]["question_type"], "long_text")
        self.assertIsNone(by_order[4]["answer"])
        # multiple_choice with no Answer -> []
        self.assertEqual(by_order[5]["question_type"], "multiple_choice")
        self.assertEqual(by_order[5]["answer"], [])

    def test_persona_resolves_for_specific_questionnaire(self):
        resp = self.client.get(
            f"/api/onboarding/responses/{self.member.email}", **self._auth(),
        )
        self.assertEqual(
            resp.json()["persona"],
            {
                "slug": "t-priya",
                "name": "Priya",
                "archetype": "The Engineer transitioning to AI",
            },
        )

    def test_blank_text_answer_serializes_as_null(self):
        rq = self._add_response_question(
            self.response, qtype="text", prompt="Blank?", order=6,
        )
        Answer.objects.create(
            response=self.response, question=rq, text_value="   ",
        )
        resp = self.client.get(
            f"/api/onboarding/responses/{self.member.email}", **self._auth(),
        )
        by_order = {q["order"]: q for q in resp.json()["questions"]}
        self.assertIsNone(by_order[6]["answer"])


class GenericFallbackPersonaTest(_OnboardingApiBase):
    """A response on onboarding-general resolves to a null persona."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.generic = cls._make_questionnaire("t-general")
        # An active persona pointing at a DIFFERENT questionnaire -- proves
        # null is about "no persona points HERE", not "no personas exist".
        other = cls._make_questionnaire("t-engineer")
        Persona.objects.create(
            name="Priya", archetype="Engineer", slug="t-priya",
            default_questionnaire=other, is_active=True,
        )
        cls.member = User.objects.create_user(email="gen@test.com", password="pw")
        cls.response = Response.objects.create(
            questionnaire=cls.generic, respondent=cls.member,
            status="submitted", submitted_at=timezone.now(),
        )
        cls._add_response_question(
            cls.response, qtype="text", prompt="Role?", order=0,
        )

    def test_generic_questionnaire_resolves_to_null_persona(self):
        resp = self.client.get(
            f"/api/onboarding/responses/{self.member.email}", **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["persona"])


class SnapshotFidelityTest(_OnboardingApiBase):
    """Editing/deleting the base question after submit must not change output."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.q = cls._make_questionnaire("t-engineer")
        cls.base_q = Question.objects.create(
            questionnaire=cls.q, question_type="multiple_choice",
            prompt="ORIGINAL prompt", order=0,
        )
        cls.base_opt_keep = QuestionOption.objects.create(
            question=cls.base_q, label="Keep", order=0,
        )
        cls.base_opt_del = QuestionOption.objects.create(
            question=cls.base_q, label="Delete me", order=1,
        )
        cls.member = User.objects.create_user(email="snap@test.com", password="pw")
        cls.response = Response.objects.create(
            questionnaire=cls.q, respondent=cls.member,
            status="submitted", submitted_at=timezone.now(),
        )
        # Snapshot rows mirror the base set at fill-in time.
        cls.rq = cls._add_response_question(
            cls.response, qtype="multiple_choice", prompt="ORIGINAL prompt",
            order=0, source=cls.base_q,
        )
        cls.snap_keep = cls._add_rq_option(
            cls.rq, "Keep", 0, source=cls.base_opt_keep,
        )
        cls.snap_del = cls._add_rq_option(
            cls.rq, "Delete me", 1, source=cls.base_opt_del,
        )
        ans = Answer.objects.create(response=cls.response, question=cls.rq)
        ans.selected_options.set([cls.snap_keep, cls.snap_del])

    def test_base_edit_does_not_change_api_output(self):
        # Mutate the base layer after submission.
        self.base_q.prompt = "EDITED prompt"
        self.base_q.save(update_fields=["prompt"])
        self.base_opt_del.delete()

        resp = self.client.get(
            f"/api/onboarding/responses/{self.member.email}", **self._auth(),
        )
        question = resp.json()["questions"][0]
        # Original prompt preserved (read from ResponseQuestion).
        self.assertEqual(question["prompt"], "ORIGINAL prompt")
        # Original selected labels preserved (read from ResponseQuestionOption).
        self.assertEqual(question["answer"], ["Keep", "Delete me"])


class BulkFeedTest(_OnboardingApiBase):
    """B2: status default, since, persona filter, pagination, total count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.q = cls._make_questionnaire("t-engineer")
        cls.persona = Persona.objects.create(
            name="Priya", archetype="Engineer", slug="t-priya",
            default_questionnaire=cls.q, is_active=True,
        )
        cls.generic = cls._make_questionnaire("t-general")

        cls.t1 = timezone.now() - timedelta(days=3)
        cls.t2 = timezone.now() - timedelta(days=2)
        cls.t3 = timezone.now() - timedelta(days=1)

        cls.r1 = cls._submitted("m1@test.com", cls.q, cls.t1)
        cls.r2 = cls._submitted("m2@test.com", cls.q, cls.t2)
        cls.r3 = cls._submitted("m3@test.com", cls.generic, cls.t3)

        # A draft -- excluded from the default feed.
        cls.draft = Response.objects.create(
            questionnaire=cls.q,
            respondent=User.objects.create_user(email="d@test.com", password="pw"),
            status="draft",
        )
        cls._add_response_question(cls.draft, qtype="text", prompt="Role?", order=0)

    @classmethod
    def _submitted(cls, email, questionnaire, submitted_at):
        member = User.objects.create_user(email=email, password="pw")
        r = Response.objects.create(
            questionnaire=questionnaire, respondent=member,
            status="submitted", submitted_at=submitted_at,
        )
        cls._add_response_question(r, qtype="text", prompt="Role?", order=0)
        return r

    def test_default_excludes_draft(self):
        resp = self.client.get("/api/onboarding/responses", **self._auth())
        data = resp.json()
        self.assertEqual(data["count"], 3)
        emails = {r["email"] for r in data["responses"]}
        self.assertNotIn("d@test.com", emails)

    def test_status_all_includes_draft(self):
        resp = self.client.get(
            "/api/onboarding/responses?status=all", **self._auth(),
        )
        emails = {r["email"] for r in resp.json()["responses"]}
        self.assertIn("d@test.com", emails)

    def test_status_draft_returns_only_drafts(self):
        resp = self.client.get(
            "/api/onboarding/responses?status=draft", **self._auth(),
        )
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["responses"][0]["email"], "d@test.com")

    def test_since_and_pagination_with_total_count(self):
        # Pass via ``data=`` so the ``+00:00`` offset is URL-encoded (a raw
        # ``+`` in a query string decodes to a space).
        since = (self.t1 + timedelta(hours=1)).isoformat()
        resp = self.client.get(
            "/api/onboarding/responses",
            data={"since": since, "limit": "1"},
            **self._auth(),
        )
        data = resp.json()
        # r2 and r3 are at/after since; r1 excluded. Total before slicing = 2.
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["responses"]), 1)
        self.assertEqual(data["limit"], 1)
        self.assertEqual(data["offset"], 0)
        # Newest-first -> first page is r3.
        self.assertEqual(data["responses"][0]["email"], "m3@test.com")

        resp2 = self.client.get(
            "/api/onboarding/responses",
            data={"since": since, "limit": "1", "offset": "1"},
            **self._auth(),
        )
        data2 = resp2.json()
        self.assertEqual(data2["count"], 2)
        self.assertEqual(data2["responses"][0]["email"], "m2@test.com")

    def test_persona_slug_filter(self):
        resp = self.client.get(
            "/api/onboarding/responses?status=all&persona=t-priya", **self._auth(),
        )
        data = resp.json()
        emails = {r["email"] for r in data["responses"]}
        # r1, r2 (engineer questionnaire) + the draft are priya; r3 generic.
        self.assertNotIn("m3@test.com", emails)
        self.assertIn("m1@test.com", emails)
        for r in data["responses"]:
            self.assertEqual(r["persona"]["slug"], "t-priya")

    def test_persona_none_filter(self):
        resp = self.client.get(
            "/api/onboarding/responses?status=all&persona=none", **self._auth(),
        )
        data = resp.json()
        emails = {r["email"] for r in data["responses"]}
        self.assertEqual(emails, {"m3@test.com"})
        self.assertIsNone(data["responses"][0]["persona"])

    def test_unknown_persona_slug_422(self):
        resp = self.client.get(
            "/api/onboarding/responses?persona=ghost", **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "validation_error")

    def test_unknown_status_422(self):
        resp = self.client.get(
            "/api/onboarding/responses?status=done", **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["allowed"], ["draft", "submitted", "all"])

    def test_bad_since_422(self):
        resp = self.client.get(
            "/api/onboarding/responses?since=notadate", **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_bulk_only_considers_onboarding_purpose(self):
        feedback_q = self._make_questionnaire("t-sprint-feedback", purpose="feedback")
        self._submitted("fb@test.com", feedback_q, self.t2)
        resp = self.client.get(
            "/api/onboarding/responses?status=all", **self._auth(),
        )
        emails = {r["email"] for r in resp.json()["responses"]}
        self.assertNotIn("fb@test.com", emails)


class QuestionnairesDefinitionTest(_OnboardingApiBase):
    """A1: nested ordered questions + options from the BASE template."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.q = cls._make_questionnaire(
            "t-engineer", description="For engineers.",
        )
        q_text = Question.objects.create(
            questionnaire=cls.q, question_type="text", prompt="Role?",
            is_required=True, order=0,
        )
        q_choice = Question.objects.create(
            questionnaire=cls.q, question_type="multiple_choice",
            prompt="Interests?", order=1,
        )
        QuestionOption.objects.create(question=q_choice, label="LLM apps", order=0)
        QuestionOption.objects.create(question=q_choice, label="MLOps", order=1)
        cls.q_text = q_text
        # A feedback questionnaire for the purpose filter test.
        cls.fb = cls._make_questionnaire("t-sprint-fb", purpose="feedback")

    def test_nested_questions_and_options_ordered(self):
        resp = self.client.get(
            "/api/onboarding/questionnaires?purpose=onboarding", **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        eng = next(
            qn for qn in data["questionnaires"]
            if qn["slug"] == "t-engineer"
        )
        self.assertEqual(eng["question_count"], 2)
        prompts = [q["prompt"] for q in eng["questions"]]
        self.assertEqual(prompts, ["Role?", "Interests?"])

        text_q = eng["questions"][0]
        self.assertEqual(text_q["question_type"], "text")
        self.assertTrue(text_q["is_required"])
        self.assertEqual(text_q["options"], [])

        choice_q = eng["questions"][1]
        labels = [o["label"] for o in choice_q["options"]]
        self.assertEqual(labels, ["LLM apps", "MLOps"])
        self.assertEqual([o["order"] for o in choice_q["options"]], [0, 1])

    def test_purpose_filter_excludes_others(self):
        resp = self.client.get(
            "/api/onboarding/questionnaires?purpose=onboarding", **self._auth(),
        )
        slugs = {qn["slug"] for qn in resp.json()["questionnaires"]}
        self.assertIn("t-engineer", slugs)
        self.assertNotIn("t-sprint-fb", slugs)

    def test_unknown_purpose_422_with_allowed(self):
        resp = self.client.get(
            "/api/onboarding/questionnaires?purpose=bogus", **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(
            body["details"]["allowed"], ["onboarding", "feedback", "general"],
        )

    def test_active_filter(self):
        self.q.is_active = False
        self.q.save(update_fields=["is_active"])
        resp = self.client.get(
            "/api/onboarding/questionnaires?active=true", **self._auth(),
        )
        slugs = {qn["slug"] for qn in resp.json()["questionnaires"]}
        self.assertNotIn("t-engineer", slugs)


class PersonasDefinitionTest(_OnboardingApiBase):
    """A2: archetype + linked questionnaire slug, active filter."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.linked_q = cls._make_questionnaire("t-engineer")
        cls.active = Persona.objects.create(
            name="Priya", archetype="The Engineer transitioning to AI",
            slug="t-priya", description="Mid-career engineer.",
            default_questionnaire=cls.linked_q, is_active=True, order=0,
        )
        cls.inactive = Persona.objects.create(
            name="Sam", archetype="The Analyst", slug="t-sam",
            default_questionnaire=None, is_active=False, order=1,
        )

    def test_lists_both_with_slug_or_null(self):
        resp = self.client.get("/api/onboarding/personas", **self._auth())
        self.assertEqual(resp.status_code, 200)
        by_slug = {p["slug"]: p for p in resp.json()["personas"]}
        self.assertEqual(
            by_slug["t-priya"]["archetype"], "The Engineer transitioning to AI",
        )
        self.assertEqual(by_slug["t-priya"]["default_questionnaire"], "t-engineer")
        self.assertIsNone(by_slug["t-sam"]["default_questionnaire"])

    def test_active_filter_returns_only_active(self):
        # The seed migration also creates active personas, so assert on the
        # two we authored rather than exact-set equality: the active one is
        # present and the inactive one is filtered out.
        resp = self.client.get(
            "/api/onboarding/personas?active=true", **self._auth(),
        )
        slugs = {p["slug"] for p in resp.json()["personas"]}
        self.assertIn("t-priya", slugs)
        self.assertNotIn("t-sam", slugs)
        for p in resp.json()["personas"]:
            self.assertTrue(p["is_active"])


class NotFoundTest(_OnboardingApiBase):
    """B1: distinct 404s for unknown email vs no onboarding response."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.known = User.objects.create_user(email="known@test.com", password="pw")

    def test_unknown_email_user_not_found(self):
        resp = self.client.get(
            "/api/onboarding/responses/ghost@nowhere.com", **self._auth(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "user_not_found")

    def test_known_user_no_response_distinct_404(self):
        resp = self.client.get(
            "/api/onboarding/responses/known@test.com", **self._auth(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "onboarding_response_not_found")

    def test_email_lookup_case_insensitive(self):
        q = self._make_questionnaire("t-engineer")
        Response.objects.create(
            questionnaire=q, respondent=self.known,
            status="submitted", submitted_at=timezone.now(),
        )
        resp = self.client.get(
            "/api/onboarding/responses/KNOWN@test.com", **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "known@test.com")

    def test_non_onboarding_response_is_404(self):
        # A feedback response for a known user must NOT satisfy the lookup.
        fb = self._make_questionnaire("t-sprint-fb", purpose="feedback")
        Response.objects.create(
            questionnaire=fb, respondent=self.known,
            status="submitted", submitted_at=timezone.now(),
        )
        resp = self.client.get(
            "/api/onboarding/responses/known@test.com", **self._auth(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], "onboarding_response_not_found")


class AuthAndMethodTest(_OnboardingApiBase):
    """All four endpoints reject missing/non-staff tokens and non-GET."""

    PATHS = [
        "/api/onboarding/questionnaires",
        "/api/onboarding/personas",
        "/api/onboarding/responses",
        "/api/onboarding/responses/any@test.com",
    ]

    def test_missing_token_401(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_nonstaff_token_401(self):
        headers = {"HTTP_AUTHORIZATION": f"Token {self.nonstaff_token.key}"}
        for path in self.PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, **headers).status_code, 401)

    def test_non_get_405(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                resp = self.client.post(path, **self._auth())
                self.assertEqual(resp.status_code, 405)


class OpenApiSyncTest(_OnboardingApiBase):
    """The four Onboarding paths are in the committed spec."""

    def test_onboarding_paths_present(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        spec = build_spec(urlpatterns)
        paths = spec["paths"]
        for path in [
            "/api/onboarding/questionnaires",
            "/api/onboarding/personas",
            "/api/onboarding/responses",
            "/api/onboarding/responses/{email}",
        ]:
            self.assertIn(path, paths)
            self.assertIn("get", paths[path])
            self.assertEqual(paths[path]["get"]["tags"], ["Onboarding"])
