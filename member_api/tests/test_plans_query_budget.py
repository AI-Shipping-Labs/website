"""Query-budget coverage for member plan list and detail APIs."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import MemberAPIKey
from member_api.serializers.plans import (
    serialize_member_plan_detail,
    serialize_member_plan_summary,
)
from member_api.views.plans import (
    _owned_plan_detail_queryset,
    _owned_plans_list_queryset,
)
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
    WeekNote,
)

User = get_user_model()

LIST_BUDGET = 8
DETAIL_BUDGET = 12
UNUSED_NESTED_TABLES = (
    "plans_resource",
    "plans_deliverable",
    "plans_nextstep",
    "plans_weeknote",
)


def _sql(query):
    return " ".join(query["sql"].split()).lower()


def _create_sprint(slug):
    return Sprint.objects.create(
        name=slug,
        slug=slug,
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=6,
        status="active",
    )


def _create_nested_plan(
    member,
    *,
    slug,
    weeks=4,
    checkpoints_per_week=5,
    resources=3,
    deliverables=2,
    next_steps=2,
    done_per_week=1,
):
    plan = Plan.objects.create(
        member=member,
        sprint=_create_sprint(slug),
        title=slug,
        goal="Ship query-budget coverage",
    )
    now = timezone.now()
    week_rows = [
        Week(plan=plan, week_number=index, theme=f"Week {index}", position=index)
        for index in range(1, weeks + 1)
    ]
    Week.objects.bulk_create(week_rows)
    week_rows = list(plan.weeks.order_by("position", "week_number"))

    checkpoints = []
    notes = []
    for week in week_rows:
        for position in range(checkpoints_per_week):
            checkpoints.append(
                Checkpoint(
                    week=week,
                    description=f"{slug} w{week.week_number} c{position}",
                    position=position,
                    done_at=now if position < done_per_week else None,
                )
            )
        notes.append(
            WeekNote(week=week, author=member, body=f"{slug} note {week.week_number}")
        )
    Checkpoint.objects.bulk_create(checkpoints)
    WeekNote.objects.bulk_create(notes)
    Resource.objects.bulk_create(
        [
            Resource(
                plan=plan,
                title=f"{slug} resource {index}",
                url=f"https://example.com/{slug}/{index}",
                position=index,
            )
            for index in range(resources)
        ]
    )
    Deliverable.objects.bulk_create(
        [
            Deliverable(
                plan=plan,
                description=f"{slug} deliverable {index}",
                position=index,
            )
            for index in range(deliverables)
        ]
    )
    NextStep.objects.bulk_create(
        [
            NextStep(
                plan=plan,
                description=f"{slug} next {index}",
                position=index,
            )
            for index in range(next_steps)
        ]
    )
    return plan


def _double_checkpoints(plan):
    extras = []
    for week in plan.weeks.all():
        start = week.checkpoints.count()
        for position in range(start, start * 2):
            extras.append(
                Checkpoint(
                    week=week,
                    description=f"extra {week.week_number}-{position}",
                    position=position,
                )
            )
    Checkpoint.objects.bulk_create(extras)


@tag("core")
class MemberPlansQueryBudgetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="plan-budget-owner@test.com",
            first_name="Owner",
        )
        cls.other = User.objects.create_user(
            email="plan-budget-other@test.com",
            first_name="Other",
        )
        _, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member,
            name="plan budget",
        )
        cls.plans = [
            _create_nested_plan(cls.member, slug=f"budget-plan-{index}")
            for index in range(5)
        ]
        cls.other_plan = _create_nested_plan(cls.other, slug="budget-other-plan")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.plaintext}"}

    def _list(self):
        return self.client.get("/member-api/v1/plans", **self._auth())

    def _detail(self, plan):
        return self.client.get(f"/member-api/v1/plans/{plan.id}", **self._auth())

    def test_list_stays_within_query_budget_when_checkpoints_double(self):
        self._list()
        with CaptureQueriesContext(connection) as captured:
            response = self._list()

        payload = response.json()
        self.assertLessEqual(len(captured.captured_queries), LIST_BUDGET)
        self.assertEqual(len(payload["plans"]), 5)
        self.assertEqual(payload["pagination"]["total"], 5)
        self.assertNotIn(self.other_plan.id, {row["id"] for row in payload["plans"]})
        for row in payload["plans"]:
            self.assertEqual(row["progress"]["checkpoints_total"], 20)
            self.assertEqual(row["progress"]["checkpoints_done"], 4)
        joined_sql = " ".join(_sql(query) for query in captured.captured_queries)
        for table in UNUSED_NESTED_TABLES:
            self.assertNotIn(table, joined_sql)

        for plan in self.plans:
            _double_checkpoints(plan)

        with CaptureQueriesContext(connection) as doubled:
            doubled_response = self._list()

        doubled_payload = doubled_response.json()
        self.assertLessEqual(len(doubled.captured_queries), len(captured.captured_queries))
        self.assertEqual(len(doubled_payload["plans"]), 5)
        for row in doubled_payload["plans"]:
            self.assertEqual(row["progress"]["checkpoints_total"], 40)
            self.assertEqual(row["progress"]["checkpoints_done"], 4)

    def test_detail_stays_within_query_budget_when_checkpoints_double(self):
        plan = self.plans[0]
        self._detail(plan)

        with CaptureQueriesContext(connection) as captured:
            response = self._detail(plan)

        payload = response.json()
        self.assertLessEqual(len(captured.captured_queries), DETAIL_BUDGET)
        self.assertEqual(len(payload["weeks"]), 4)
        self.assertEqual(
            [week["week_number"] for week in payload["weeks"]],
            [1, 2, 3, 4],
        )
        self.assertEqual(len(payload["weeks"][0]["checkpoints"]), 5)
        self.assertEqual(
            [item["position"] for item in payload["weeks"][0]["checkpoints"]],
            [0, 1, 2, 3, 4],
        )
        self.assertEqual(payload["weeks"][0]["note"]["body"], "budget-plan-0 note 1")
        self.assertEqual(len(payload["resources"]), 3)
        self.assertEqual(len(payload["deliverables"]), 2)
        self.assertEqual(len(payload["next_steps"]), 2)
        self.assertEqual(payload["progress"]["checkpoints_total"], 20)
        self.assertEqual(payload["progress"]["checkpoints_done"], 4)

        _double_checkpoints(plan)

        with CaptureQueriesContext(connection) as doubled:
            doubled_response = self._detail(plan)

        doubled_payload = doubled_response.json()
        self.assertLessEqual(len(doubled.captured_queries), len(captured.captured_queries))
        self.assertEqual(len(doubled_payload["weeks"][0]["checkpoints"]), 10)
        self.assertEqual(doubled_payload["progress"]["checkpoints_total"], 40)
        self.assertIsNotNone(doubled_payload["weeks"][0]["note"])
        self.assertEqual(len(doubled_payload["resources"]), 3)
        self.assertEqual(len(doubled_payload["deliverables"]), 2)
        self.assertEqual(len(doubled_payload["next_steps"]), 2)

    def test_serializers_use_annotated_and_prefetched_caches(self):
        plan = self.plans[0]
        summary_plan = _owned_plans_list_queryset(self.member).get(pk=plan.pk)
        with self.assertNumQueries(0):
            summary = serialize_member_plan_summary(summary_plan)
        self.assertEqual(summary["progress"]["checkpoints_total"], 20)
        self.assertEqual(summary["progress"]["checkpoints_done"], 4)

        detail_plan = _owned_plan_detail_queryset(self.member).get(pk=plan.pk)
        with self.assertNumQueries(0):
            detail = serialize_member_plan_detail(detail_plan)
        self.assertEqual(len(detail["weeks"]), 4)
        self.assertEqual(len(detail["weeks"][0]["checkpoints"]), 5)
        self.assertIsNotNone(detail["weeks"][0]["note"])
        self.assertEqual(len(detail["resources"]), 3)

    def test_progress_write_does_not_prefetch_unused_nested_graphs(self):
        plan = self.plans[0]
        checkpoint = (
            plan.weeks.order_by("position", "week_number")
            .first()
            .checkpoints.filter(done_at__isnull=True)
            .order_by("position", "id")
            .first()
        )
        self._detail(plan)

        with CaptureQueriesContext(connection) as captured:
            response = self.client.patch(
                f"/member-api/v1/plans/{plan.id}/progress",
                data=json.dumps({
                    "checkpoints": [{"id": checkpoint.id, "done": True}],
                }),
                content_type="application/json",
                **self._auth(),
            )

        self.assertEqual(response.json()["progress"]["checkpoints_done"], 5)
        joined_sql = " ".join(_sql(query) for query in captured.captured_queries)
        self.assertNotIn("plans_resource", joined_sql)
        self.assertNotIn("plans_weeknote", joined_sql)

        Resource.objects.bulk_create(
            [
                Resource(
                    plan=plan,
                    title=f"extra resource {index}",
                    url=f"https://example.com/extra/{index}",
                    position=10 + index,
                )
                for index in range(20)
            ]
        )

        with CaptureQueriesContext(connection) as doubled:
            doubled_response = self.client.patch(
                f"/member-api/v1/plans/{plan.id}/progress",
                data=json.dumps({
                    "checkpoints": [{"id": checkpoint.id, "done": False}],
                }),
                content_type="application/json",
                **self._auth(),
            )

        self.assertEqual(doubled_response.json()["progress"]["checkpoints_done"], 4)
        self.assertLessEqual(len(doubled.captured_queries), len(captured.captured_queries))
