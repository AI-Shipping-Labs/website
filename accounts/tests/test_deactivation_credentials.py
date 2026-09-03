"""Deactivation must revoke API credentials and not restore them on reactivation."""

from django.test import TestCase
from django.utils import timezone

from accounts.models import MemberAPIKey, Token, User


class DeactivationCredentialLifecycleTest(TestCase):
    def test_save_is_active_false_revokes_keys_deletes_tokens_and_stays_revoked(self):
        staff = User.objects.create_user(
            email="deactivate-staff@test.com",
            password="pw",
            is_staff=True,
        )
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=staff,
            name="active key",
        )
        already_revoked, revoked_plaintext = MemberAPIKey.create_for_user(
            user=staff,
            name="already revoked",
        )
        already_revoked.revoke()
        original_revoked_at = already_revoked.revoked_at
        token, token_plaintext = Token.create_for_user(
            user=staff,
            name="operator",
        )

        self.assertIsNotNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNotNone(Token.authenticate(token_plaintext))

        staff.is_active = False
        staff.save(update_fields=["is_active"])

        member_key.refresh_from_db()
        already_revoked.refresh_from_db()
        self.assertIsNotNone(member_key.revoked_at)
        self.assertEqual(already_revoked.revoked_at, original_revoked_at)
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())
        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNone(MemberAPIKey.authenticate(revoked_plaintext))
        self.assertIsNone(Token.authenticate(token_plaintext))

        first_revoked_at = member_key.revoked_at
        staff.save(update_fields=["is_active"])
        member_key.refresh_from_db()
        already_revoked.refresh_from_db()
        self.assertEqual(member_key.revoked_at, first_revoked_at)
        self.assertEqual(already_revoked.revoked_at, original_revoked_at)
        self.assertFalse(Token.objects.filter(user=staff).exists())

        staff.is_active = True
        staff.save(update_fields=["is_active"])
        member_key.refresh_from_db()
        self.assertIsNotNone(member_key.revoked_at)
        self.assertEqual(member_key.revoked_at, first_revoked_at)
        self.assertFalse(Token.objects.filter(user=staff).exists())
        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNone(Token.authenticate(token_plaintext))

    def test_queryset_update_does_not_revoke_but_authenticate_still_rejects(self):
        staff = User.objects.create_user(
            email="queryset-inactive@test.com",
            password="pw",
            is_staff=True,
        )
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=staff,
            name="surviving key",
        )
        token, token_plaintext = Token.create_for_user(
            user=staff,
            name="surviving token",
        )

        User.objects.filter(pk=staff.pk).update(is_active=False)

        member_key.refresh_from_db()
        token.refresh_from_db()
        self.assertIsNone(member_key.revoked_at)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNone(Token.authenticate(token_plaintext))

    def test_demoted_staff_token_is_still_deleted_on_deactivation(self):
        user = User.objects.create_user(
            email="demoted-deactivate@test.com",
            password="pw",
            is_staff=True,
        )
        token, plaintext = Token.create_for_user(user=user, name="while staff")
        user.is_staff = False
        user.save(update_fields=["is_staff"])
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

        user.is_active = False
        user.save()

        self.assertFalse(Token.objects.filter(pk=token.pk).exists())
        self.assertIsNone(Token.authenticate(plaintext))

    def test_saving_unrelated_fields_does_not_revoke_credentials(self):
        staff = User.objects.create_user(
            email="unrelated-save@test.com",
            password="pw",
            is_staff=True,
        )
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=staff,
            name="keep active",
        )
        token, token_plaintext = Token.create_for_user(
            user=staff,
            name="keep token",
        )
        staff.is_active = False
        staff.save(update_fields=["unsubscribed"])

        staff.refresh_from_db()
        member_key.refresh_from_db()
        self.assertTrue(staff.is_active)
        self.assertIsNone(member_key.revoked_at)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
        self.assertIsNotNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNotNone(Token.authenticate(token_plaintext))

    def test_already_inactive_save_is_a_noop_for_credentials(self):
        staff = User.objects.create_user(
            email="already-inactive@test.com",
            password="pw",
            is_staff=True,
            is_active=False,
        )
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=staff,
            name="created while inactive",
        )
        token = Token(
            key="already-inactive-operator-token-key",
            user=staff,
            name="legacy inactive token",
        )
        Token.objects.bulk_create([token])
        token_plaintext = token.key

        before = timezone.now()
        staff.save(update_fields=["is_active"])

        member_key.refresh_from_db()
        self.assertIsNone(member_key.revoked_at)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
        self.assertLessEqual(member_key.created_at, before)
        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNone(Token.authenticate(token_plaintext))
