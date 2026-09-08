"""Indexed contact-tag relation, synchronization, and query budgets."""

import importlib
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import ContactTag
from accounts.utils.tags import (
    TAG_MUTATION_CHUNK_SIZE,
    add_tag,
    delete_tag,
    list_all_tags,
    remove_tag,
    rename_tag,
    set_tags,
    tags_with_user_counts,
    user_ids_matching_tag_search,
    user_ids_with_exact_tag,
)

User = get_user_model()


def _relation_slugs(user):
    return set(user.contact_tags.values_list("slug", flat=True))


class ContactTagSchemaTest(TestCase):
    def test_slug_and_through_table_have_unique_indexed_keys(self):
        slug_field = ContactTag._meta.get_field("slug")
        self.assertTrue(slug_field.unique)

        through = User.contact_tags.through
        self.assertIn(
            ("user", "contacttag"),
            through._meta.unique_together,
        )
        self.assertTrue(through._meta.get_field("user").db_index)
        self.assertTrue(through._meta.get_field("contacttag").db_index)


class ContactTagSynchronizationTest(TestCase):
    def test_add_and_remove_keep_both_representations_equal(self):
        user = User.objects.create_user(email="add-remove@test.com")

        add_tag(user, "Early Adopter")
        add_tag(user, "early-adopter")
        user.refresh_from_db()
        self.assertEqual(user.tags, ["early-adopter"])
        self.assertEqual(_relation_slugs(user), set(user.tags))

        remove_tag(user, "Early Adopter")
        remove_tag(user, "early-adopter")
        user.refresh_from_db()
        self.assertEqual(user.tags, [])
        self.assertEqual(_relation_slugs(user), set())
        self.assertFalse(ContactTag.objects.filter(slug="early-adopter").exists())

    def test_save_with_tags_normalizes_and_syncs_relation(self):
        user = User.objects.create_user(
            email="save-sync@test.com",
            tags=["Early Adopter", "early-adopter", "Stripe:Active"],
        )

        user.refresh_from_db()
        self.assertEqual(user.tags, ["early-adopter", "stripe:active"])
        self.assertEqual(_relation_slugs(user), set(user.tags))

    def test_tag_sync_failure_rolls_back_json_write(self):
        user = User.objects.create_user(email="fail-closed@test.com")
        user.tags = ["paid"]

        with patch(
            "accounts.utils.tags.sync_contact_tags",
            side_effect=DatabaseError("relation unavailable"),
        ):
            with self.assertRaises(DatabaseError):
                user.save(update_fields=["tags"])

        user.refresh_from_db()
        self.assertEqual(user.tags, [])
        self.assertEqual(_relation_slugs(user), set())

    def test_replace_removes_unused_rows_and_preserves_shared_rows(self):
        first = User.objects.create_user(email="first@test.com", tags=["old"])
        second = User.objects.create_user(email="second@test.com", tags=["old"])

        set_tags(first, ["new"])
        self.assertTrue(ContactTag.objects.filter(slug="old").exists())
        set_tags(second, [])

        first.refresh_from_db()
        self.assertEqual(first.tags, ["new"])
        self.assertEqual(_relation_slugs(first), {"new"})
        self.assertFalse(ContactTag.objects.filter(slug="old").exists())

    def test_delete_everywhere_removes_json_relation_and_unused_row(self):
        first = User.objects.create_user(email="delete-first@test.com", tags=["old"])
        second = User.objects.create_user(
            email="delete-second@test.com",
            tags=["old", "keep"],
        )

        result = delete_tag("old")

        self.assertEqual(result, {"affected": 2, "name": "old"})
        for user in (first, second):
            user.refresh_from_db()
            self.assertEqual(_relation_slugs(user), set(user.tags))
            self.assertNotIn("old", user.tags)
        self.assertFalse(ContactTag.objects.filter(slug="old").exists())


class ContactTagBackfillTest(TestCase):
    def test_backfill_normalizes_syncs_and_is_idempotent(self):
        user = User.objects.create_user(email="legacy-tags@test.com")
        User.objects.filter(pk=user.pk).update(
            tags=["Legacy Tag", "legacy-tag", "Stripe:Active", ""],
        )
        migration = importlib.import_module(
            "accounts.migrations.0028_contact_tags_relation",
        )
        schema_editor = SimpleNamespace(connection=connection)

        migration.backfill_contact_tags(apps, schema_editor)
        migration.backfill_contact_tags(apps, schema_editor)

        user.refresh_from_db()
        self.assertEqual(user.tags, ["legacy-tag", "stripe:active"])
        self.assertEqual(_relation_slugs(user), set(user.tags))
        self.assertEqual(ContactTag.objects.count(), 2)


class ContactTagQueryBudgetTest(TestCase):
    def _measure(self, user_count):
        tagged = User.objects.create_user(
            email=f"tagged-{user_count}@test.com",
            tags=["vip", "cohort-a"],
        )
        User.objects.create_user(
            email=f"second-{user_count}@test.com",
            tags=["vip"],
        )
        for index in range(user_count - 2):
            User.objects.create_user(
                email=f"untagged-{user_count}-{index}@test.com",
            )

        measurements = {}
        for name, operation in (
            ("list", list_all_tags),
            ("exact", lambda: list(user_ids_with_exact_tag("vip"))),
            ("substring", lambda: list(user_ids_matching_tag_search("vi"))),
        ):
            with CaptureQueriesContext(connection) as queries:
                operation()
            measurements[name] = (len(queries), [query["sql"] for query in queries])

        self.assertIn(tagged.pk, set(user_ids_with_exact_tag("vip")))
        User.objects.all().delete()
        ContactTag.objects.all().delete()
        return measurements

    def test_indexed_reads_do_not_scale_with_untagged_user_volume(self):
        small = self._measure(8)
        large = self._measure(32)

        for operation in ("list", "exact", "substring"):
            self.assertLessEqual(
                abs(large[operation][0] - small[operation][0]),
                2,
            )
            sql = " ".join(small[operation][1] + large[operation][1]).lower()
            self.assertNotIn('select "accounts_user"."tags"', sql)
        self.assertIn("accounts_user_contact_tags", " ".join(large["exact"][1]))
        self.assertIn("accounts_user_contact_tags", " ".join(large["substring"][1]))

    def test_tag_counts_are_one_annotated_query(self):
        User.objects.create_user(email="one@test.com", tags=["alpha", "beta"])
        User.objects.create_user(email="two@test.com", tags=["alpha"])

        with CaptureQueriesContext(connection) as queries:
            rows = tags_with_user_counts()

        self.assertEqual(
            rows,
            [
                {"name": "alpha", "user_count": 2},
                {"name": "beta", "user_count": 1},
            ],
        )
        self.assertEqual(len(queries), 1)

    def test_rename_uses_relation_matched_batches(self):
        tagged = User.objects.create_user(
            email="rename-tagged@test.com",
            tags=["old", "keep"],
        )
        untouched = User.objects.create_user(
            email="rename-untouched@test.com",
            tags=["other"],
        )

        with CaptureQueriesContext(connection) as queries:
            result = rename_tag("old", "new")

        self.assertEqual(TAG_MUTATION_CHUNK_SIZE, 500)
        self.assertEqual(result["affected"], 1)
        tagged.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(tagged.tags, ["new", "keep"])
        self.assertEqual(_relation_slugs(tagged), {"new", "keep"})
        self.assertEqual(untouched.tags, ["other"])
        selects = [
            query["sql"].lower()
            for query in queries
            if query["sql"].lstrip().lower().startswith("select")
            and "accounts_user" in query["sql"].lower()
        ]
        self.assertTrue(selects)
        self.assertTrue(all("accounts_user_contact_tags" in sql for sql in selects))

    def test_chunk_failure_rolls_back_entire_rename(self):
        users = [
            User.objects.create_user(
                email=f"atomic-rename-{index}@test.com",
                tags=["old"],
            )
            for index in range(3)
        ]
        original_bulk_update = QuerySet.bulk_update
        user_bulk_updates = 0

        def fail_second_user_chunk(queryset, objects, fields, **kwargs):
            nonlocal user_bulk_updates
            if queryset.model is User:
                user_bulk_updates += 1
                if user_bulk_updates == 2:
                    raise DatabaseError("second chunk failed")
            return original_bulk_update(queryset, objects, fields, **kwargs)

        with patch("accounts.utils.tags.TAG_MUTATION_CHUNK_SIZE", 2), patch.object(
            QuerySet,
            "bulk_update",
            new=fail_second_user_chunk,
        ):
            with self.assertRaises(DatabaseError):
                rename_tag("old", "new")

        for user in users:
            user.refresh_from_db()
            self.assertEqual(user.tags, ["old"])
            self.assertEqual(_relation_slugs(user), {"old"})
        self.assertFalse(ContactTag.objects.filter(slug="new").exists())
