"""The site-owned extension row and the moves that created it (A3.2, #1692)."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.utils.tags import set_tags
from accounts_ext.models import ContactTag, MemberExtra

User = get_user_model()


class MemberExtraCreationInvariantTest(TestCase):
    """Every user must end up with exactly one MemberExtra row."""

    def test_manager_created_user_gets_exactly_one_row(self):
        user = User.objects.create_user(email="extra-manager@test.com")

        self.assertEqual(MemberExtra.objects.filter(user=user).count(), 1)
        self.assertEqual(MemberExtra.objects.get(user=user).pk, user.pk)

    def test_registration_entry_point_creates_the_row(self):
        response = self.client.post(
            reverse("api_register"),
            data=json.dumps(
                {
                    "email": "extra-register@test.com",
                    "password": "sufficiently-long-passphrase",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="extra-register@test.com")
        self.assertEqual(MemberExtra.objects.filter(user=user).count(), 1)

    def test_for_user_is_idempotent_and_creates_a_missing_row(self):
        user = User.objects.create_user(email="extra-lazy@test.com")
        MemberExtra.objects.filter(user=user).delete()

        first = MemberExtra.for_user(user)
        second = MemberExtra.for_user(user)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MemberExtra.objects.filter(user=user).count(), 1)

    def test_deleting_the_user_removes_the_extension_row(self):
        user = User.objects.create_user(email="extra-cascade@test.com")
        set_tags(user, ["vip"])
        user_pk = user.pk

        user.delete()

        self.assertFalse(MemberExtra.objects.filter(user_id=user_pk).exists())


class MemberExtraMissingTableTest(TransactionTestCase):
    def test_user_create_skips_when_the_extension_table_is_missing(self):
        from django.db import connection

        with connection.schema_editor() as editor:
            editor.delete_model(MemberExtra)
        user = User.objects.create_user(email="extra-pre-migrate@test.com")
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        with connection.schema_editor() as editor:
            editor.create_model(MemberExtra)


class MovedModelTableTest(TestCase):
    """The moved models kept their tables; only the app label changed."""

    def test_contact_tag_keeps_the_accounts_table(self):
        self.assertEqual(ContactTag._meta.db_table, "accounts_contacttag")
        self.assertEqual(ContactTag._meta.app_label, "accounts_ext")

    def test_member_extra_is_keyed_on_the_user_row(self):
        user = User.objects.create_user(email="extra-pk@test.com")
        set_tags(user, ["alpha", "beta"])

        extra = MemberExtra.objects.get(user=user)

        self.assertEqual(extra.pk, user.pk)
        self.assertEqual(
            set(extra.contact_tags.values_list("slug", flat=True)),
            {"alpha", "beta"},
        )
