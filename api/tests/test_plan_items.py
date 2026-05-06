"""Tests for Resources / Deliverables / Next Steps endpoints (issue #433).

The three child collections share the same shape, so we parametrize the
shared cases (list ordering, create-appends, position reorder, delete)
across all three. The next-step ``assignee_label`` free-text test is
specific to that resource.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Deliverable, NextStep, Plan, Resource, Sprint

User = get_user_model()


class PlanItemsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="s")
        cls.member = User.objects.create_user(
            email="m@test.com", password="pw",
        )
        cls.sprint = Sprint.objects.create(
            name="s", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}


# Helper to spell out the per-type config for the parametrized tests.
ITEM_CONFIGS = [
    {
        "name": "resource",
        "model": Resource,
        "list_url": lambda plan_id: f"/api/plans/{plan_id}/resources",
        "detail_url": lambda item_id: f"/api/resources/{item_id}",
        "list_key": "resources",
        "create_payload": {
            "title": "x",
            "url": "https://x",
            "note": "y",
        },
        "create_kwargs": lambda plan, position: {
            "plan": plan, "title": "row", "position": position,
        },
    },
    {
        "name": "deliverable",
        "model": Deliverable,
        "list_url": lambda plan_id: f"/api/plans/{plan_id}/deliverables",
        "detail_url": lambda item_id: f"/api/deliverables/{item_id}",
        "list_key": "deliverables",
        "create_payload": {"description": "ship something"},
        "create_kwargs": lambda plan, position: {
            "plan": plan, "description": "row", "position": position,
        },
    },
    {
        "name": "next_step",
        "model": NextStep,
        "list_url": lambda plan_id: f"/api/plans/{plan_id}/next-steps",
        "detail_url": lambda item_id: f"/api/next-steps/{item_id}",
        "list_key": "next_steps",
        "create_payload": {
            "assignee_label": "Member",
            "description": "do thing",
        },
        "create_kwargs": lambda plan, position: {
            "plan": plan, "assignee_label": "x",
            "description": "row", "position": position,
        },
    },
]


class PlanItemsListTest(PlanItemsTestBase):
    def test_list_orders_by_position(self):
        for config in ITEM_CONFIGS:
            with self.subTest(item=config["name"]):
                Model = config["model"]
                # Wipe any rows from previous subtests.
                Model.objects.filter(plan=self.plan).delete()
                # Insert OUT of order so sorting is exercised.
                row_b = Model.objects.create(
                    **config["create_kwargs"](self.plan, 1),
                )
                row_a = Model.objects.create(
                    **config["create_kwargs"](self.plan, 0),
                )
                response = self.client.get(
                    config["list_url"](self.plan.id), **self._auth(),
                )
                self.assertEqual(response.status_code, 200)
                rows = response.json()[config["list_key"]]
                self.assertEqual(rows[0]["id"], row_a.id)
                self.assertEqual(rows[1]["id"], row_b.id)


class PlanItemsCreateAppendsTest(PlanItemsTestBase):
    def test_create_appends_position(self):
        for config in ITEM_CONFIGS:
            with self.subTest(item=config["name"]):
                Model = config["model"]
                Model.objects.filter(plan=self.plan).delete()
                Model.objects.create(
                    **config["create_kwargs"](self.plan, 0),
                )
                response = self.client.post(
                    config["list_url"](self.plan.id),
                    data=json.dumps(config["create_payload"]),
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()["position"], 1)


class PlanItemsPatchReorderTest(PlanItemsTestBase):
    def test_patch_position_reorders_siblings(self):
        for config in ITEM_CONFIGS:
            with self.subTest(item=config["name"]):
                Model = config["model"]
                Model.objects.filter(plan=self.plan).delete()
                rows = [
                    Model.objects.create(
                        **config["create_kwargs"](self.plan, i),
                    )
                    for i in range(4)
                ]
                # Move row 0 to position 2.
                response = self.client.patch(
                    config["detail_url"](rows[0].id),
                    data=json.dumps({"position": 2}),
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 200)
                for row in rows:
                    row.refresh_from_db()
                self.assertEqual(rows[1].position, 0)
                self.assertEqual(rows[2].position, 1)
                self.assertEqual(rows[0].position, 2)
                self.assertEqual(rows[3].position, 3)


class PlanItemsDeleteTest(PlanItemsTestBase):
    def test_delete_decrements_subsequent(self):
        for config in ITEM_CONFIGS:
            with self.subTest(item=config["name"]):
                Model = config["model"]
                Model.objects.filter(plan=self.plan).delete()
                rows = [
                    Model.objects.create(
                        **config["create_kwargs"](self.plan, i),
                    )
                    for i in range(4)
                ]
                response = self.client.delete(
                    config["detail_url"](rows[1].id), **self._auth(),
                )
                self.assertEqual(response.status_code, 204)
                rows[0].refresh_from_db()
                rows[2].refresh_from_db()
                rows[3].refresh_from_db()
                self.assertEqual(rows[0].position, 0)
                self.assertEqual(rows[2].position, 1)
                self.assertEqual(rows[3].position, 2)


class NextStepFreeformTest(PlanItemsTestBase):
    def test_assignee_label_stored_verbatim(self):
        response = self.client.post(
            f"/api/plans/{self.plan.id}/next-steps",
            data=json.dumps({
                "assignee_label": "Valeriia",
                "description": "Review thing",
            }),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["assignee_label"], "Valeriia")
