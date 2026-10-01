"""Query budgets for the staff endpoints that spiked prod CPU (issue #1854).

``POST /api/campaigns/recipient-count`` must stay one narrow aggregate, and
``GET /api/users/<email>`` (``asl users get``) must cost the same number of
queries however many aliases, tags, or audience rows exist.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import EmailAlias, Token
from accounts.utils.tags import add_tag
from payments.models import Tier
from tests.fixtures import create_user_with_membership

User = get_user_model()


class StaffEndpointQueryBudgetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="ops@test.com", password="x", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="agent")
        cls.main = Tier.objects.get(slug="main")
        cls.member = create_user_with_membership(
            email="member@test.com", password=None, tier=cls.main,
            email_verified=True,
        )

    def _headers(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _queries(self, call):
        call()  # warm per-process caches (redirect table, config stamp)
        with CaptureQueriesContext(connection) as ctx:
            response = call()
        return ctx.captured_queries, response

    def _user_detail(self):
        return self.client.get(f"/api/users/{self.member.email}", **self._headers())

    def _recipient_count(self):
        return self.client.post(
            "/api/campaigns/recipient-count",
            data='{"target_min_level": 20, "target_tags_none": ["bounced"]}',
            content_type="application/json",
            **self._headers(),
        )

    def test_user_detail_query_count_does_not_grow_with_related_rows(self):
        baseline, _ = self._queries(self._user_detail)

        for index in range(5):
            EmailAlias.objects.create(
                user=self.member, email=f"alias{index}@test.com",
            )
            add_tag(self.member, f"cohort-{index}")
        grown, response = self._queries(self._user_detail)

        self.assertEqual(len(response.json()["aliases"]), 5)
        self.assertEqual(len(grown), len(baseline))
        self.assertLessEqual(len(grown), 6)

    def test_recipient_count_is_one_aggregate_over_distinct_user_ids(self):
        for index in range(5):
            create_user_with_membership(
                email=f"reader{index}@test.com", password=None, tier=self.main,
                email_verified=True,
            )

        queries, response = self._queries(self._recipient_count)

        self.assertEqual(response.json()["recipient_count"], 6)
        counts = []
        for query in queries:
            if "COUNT(" in query["sql"].upper():
                counts.append(query["sql"])
        self.assertEqual(len(counts), 1)
        # Distinct over the id only, never over whole (JSON-carrying) rows.
        self.assertNotIn('"accounts_user"."email"', counts[0])
        self.assertNotIn('"accounts_user"."import_metadata"', counts[0])
