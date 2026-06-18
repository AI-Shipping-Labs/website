"""Migration coverage for folding legacy duplicate WeekNote rows."""

import datetime
import json

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class WeekNoteSingletonMigrationTest(TransactionTestCase):
    migrate_from = [("plans", "0019_nextstep_kind")]
    migrate_to = [("plans", "0020_weeknote_singleton")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_duplicate_week_notes_fold_into_latest_note(self):
        Sprint = self.apps.get_model("plans", "Sprint")
        Plan = self.apps.get_model("plans", "Plan")
        Week = self.apps.get_model("plans", "Week")
        WeekNote = self.apps.get_model("plans", "WeekNote")

        user_values = {
            "email": "legacy@test.com",
            "password": "",
            "first_name": "",
            "last_name": "",
            "date_joined": timezone.now(),
            "is_staff": False,
            "is_active": True,
            "is_superuser": False,
            "email_verified": True,
            "unsubscribed": False,
            "email_preferences": json.dumps({}),
            "stripe_customer_id": "",
            "subscription_id": "",
            "slack_user_id": "",
            "theme_preference": "",
            "preferred_timezone": "",
            "tags": json.dumps([]),
            "import_source": "manual",
            "import_metadata": json.dumps({}),
            "signup_source": "unknown",
            "account_activated": False,
            "soft_bounce_count": 0,
            "bounce_state": "none",
            "last_bounce_diagnostic": "",
            "slack_member": False,
        }
        with connection.cursor() as cursor:
            columns = [
                row[1]
                for row in cursor.execute("PRAGMA table_info(accounts_user)")
                if row[1] != "id" and row[1] in user_values
            ]
            placeholders = ", ".join(["%s"] * len(columns))
            cursor.execute(
                f"INSERT INTO accounts_user ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                [user_values[column] for column in columns],
            )
            member_id = cursor.lastrowid
        sprint = Sprint.objects.create(
            name="Legacy Sprint",
            slug="legacy-sprint",
            start_date=datetime.date(2026, 5, 1),
        )
        plan = Plan.objects.create(member_id=member_id, sprint=sprint)
        week = Week.objects.create(plan=plan, week_number=1)

        older = WeekNote.objects.create(
            week=week,
            body="older note body",
            author_id=member_id,
        )
        newer = WeekNote.objects.create(
            week=week,
            body="newer note body",
            author_id=member_id,
        )
        older.created_at = older.updated_at = datetime.datetime(
            2026, 5, 2, 12, 0, tzinfo=datetime.UTC,
        )
        newer.created_at = newer.updated_at = datetime.datetime(
            2026, 5, 3, 12, 0, tzinfo=datetime.UTC,
        )
        older.save(update_fields=["created_at", "updated_at"])
        newer.save(update_fields=["created_at", "updated_at"])

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        MigratedWeekNote = apps.get_model("plans", "WeekNote")

        notes = list(MigratedWeekNote.objects.filter(week_id=week.pk))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].pk, newer.pk)
        self.assertIn("newer note body", notes[0].body)
        self.assertIn("Earlier notes", notes[0].body)
        self.assertIn("older note body", notes[0].body)
        self.assertIn("legacy@test.com", notes[0].body)
