"""Source-side contract for the nightly business-metrics collection.

The collect-only SQL in coolify-observability ``biz-metrics/sql/aisl/``
(Hub intake 8cc61eb4, t1127u) aggregates this database directly for
lesson opens, signups, paid conversions and new MRR. A table, column or
event-literal rename here would silently break the nightly run, so this
module locks the exact names and literals the SQL depends on, plus the
join keys the app writes.

The recording behaviors themselves are covered where they live
(``test_user_activity``, ``test_signup_attribution``,
``test_conversion_attribution``); this module only guards the
cross-repo contract.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from analytics.activity import record_lesson_open
from analytics.models import UserActivity, UserAttribution
from content.models import Course, Module, Unit
from payments.models import ConversionAttribution, Tier

User = get_user_model()

EXPECTED_TABLES = (
    (UserActivity, "analytics_useractivity"),
    (UserAttribution, "analytics_userattribution"),
    (ConversionAttribution, "payments_conversionattribution"),
    (Tier, "payments_tier"),
    (Course, "cb_curriculum_course"),
    (Module, "cb_curriculum_module"),
    (Unit, "cb_curriculum_unit"),
)

# (model, field name, database column) triples read by the SQL files:
# lesson opens join unit/module/course by PK, signups join the
# attribution row, conversions aggregate tier/billing/mrr per day.
EXPECTED_COLUMNS = (
    (UserActivity, "user", "user_id"),
    (UserActivity, "event_type", "event_type"),
    (UserActivity, "occurred_at", "occurred_at"),
    (UserActivity, "object_type", "object_type"),
    (UserActivity, "object_id", "object_id"),
    (UserAttribution, "user", "user_id"),
    (UserAttribution, "first_touch_utm_source", "first_touch_utm_source"),
    (Unit, "id", "id"),
    (Unit, "module", "module_id"),
    (Module, "id", "id"),
    (Module, "course", "course_id"),
    (Course, "id", "id"),
    (Course, "slug", "slug"),
    (ConversionAttribution, "created_at", "created_at"),
    (ConversionAttribution, "tier", "tier_id"),
    (ConversionAttribution, "billing_period", "billing_period"),
    (ConversionAttribution, "mrr_eur", "mrr_eur"),
    (ConversionAttribution, "first_touch_utm_source", "first_touch_utm_source"),
    (Tier, "id", "id"),
    (Tier, "slug", "slug"),
)

# String literals the SQL filters on. Pinned as literals (not via the
# symbolic constants) so a constant rename breaks this test instead of
# silently zeroing the nightly metrics.
EXPECTED_EVENT_LITERALS = (
    (UserActivity.EVENT_LESSON_OPEN, "lesson_open"),
    (UserActivity.EVENT_SIGNUP, "signup"),
    (UserActivity.EVENT_RESOURCE_VIEW, "resource_view"),
)


class BizMetricsSourceContractTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="contract@test.com",
            password="pw",
        )
        cls.course = Course.objects.create(
            title="Contract",
            slug="contract",
            status="published",
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title="M1",
            slug="m1",
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title="Intro",
            slug="intro",
            sort_order=1,
        )

    def test_tables_match_external_sql_contract(self):
        for model, table in EXPECTED_TABLES:
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.db_table, table)

    def test_columns_match_external_sql_contract(self):
        for model, field_name, column in EXPECTED_COLUMNS:
            with self.subTest(model=model.__name__, field=field_name):
                field = model._meta.get_field(field_name)
                self.assertEqual(field.column, column)

    def test_event_literals_match_external_sql_contract(self):
        for constant, literal in EXPECTED_EVENT_LITERALS:
            with self.subTest(literal=literal):
                self.assertEqual(constant, literal)

    def test_lesson_open_row_carries_sql_join_keys(self):
        row = record_lesson_open(self.user, unit=self.unit)
        self.assertIsNotNone(row)
        self.assertEqual(row.event_type, "lesson_open")
        self.assertEqual(row.object_type, "unit")
        self.assertEqual(row.object_id, str(self.unit.pk))
