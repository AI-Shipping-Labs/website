"""An account merge revokes a ``community_base.api.APIKey``, never moves it.

Provenance: this file started life as the uncommitted characterization suite
``accounts/tests/test_merge_cb_api_key_1656.py`` in the worktree
``/data/agents/ai-shipping-labs/worktrees/verify-1656`` (branch
``verify-1656-credentials``). Those three tests executed the real merge service
against a real ``cb_api.APIKey`` row and pinned the BUGGY behaviour reported in
issue #1736: the key was repointed onto the surviving account with
``revoked_at`` still ``NULL``, and the merge report said nothing happened. They
were mutation-checked -- adding the missing strategy made all three fail. They
are adopted here with their assertions inverted, so the same executed behaviour
now guards the fix instead of the bug.

The contract they now pin:

``accounts/services/account_merge.py`` registers explicit credential strategies
for ``accounts.Token`` (delete) and ``accounts.MemberAPIKey`` (revoke).
``community_base.api.APIKey`` exposes ``user`` as a plain reverse FK on ``User``
(``related_name="community_base_api_keys"``), so without an entry in
``_SPECIAL_STRATEGIES`` it falls through to the generic repoint branch and a
LIVE secret changes hands. ``_strategy_package_api_key`` closes that: the row
stays owned by the retired secondary and is revoked in place, and the count
surfaces as ``MergePlan.credentials["package_api_keys_revoked"]``.

The final test is a structural guard so the next credential model cannot slip
through the same way without anyone reading ``_SPECIAL_STRATEGIES``.
"""

import json

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.test import TestCase

from accounts.services.account_merge import (
    _SPECIAL_STRATEGIES,
    AUDIT_ACTION,
    merge_accounts,
)
from community.models import CommunityAuditLog
from payments.models import Tier

User = get_user_model()


class MergeRevokesCommunityBaseApiKeyTest(TestCase):
    """Execute a real merge with a real package API key attached."""

    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0}
        )

    def setUp(self):
        self.canonical = User.objects.create_user(
            email="keep-1736@test.com", password="x"
        )
        self.secondary = User.objects.create_user(
            email="dupe-1736@test.com", password="x"
        )

    def test_member_key_owned_by_secondary_is_revoked_in_place(self):
        """Inverts ``test_member_key_owned_by_secondary_moves_to_canonical_unrevoked``."""
        key, plaintext = APIKey.create_for_user(
            user=self.secondary,
            name="dupe member key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        merge_accounts(
            self.canonical, self.secondary, actor_label="test:1736"
        )

        key.refresh_from_db()
        # Revoked, and still owned by the retired identity as durable security
        # history -- exactly what _strategy_member_api_key does for
        # accounts.MemberAPIKey.
        self.assertEqual(key.user_id, self.secondary.pk)
        self.assertIsNotNone(key.revoked_at)
        # The old secret is dead rather than reassigned.
        self.assertIsNone(APIKey.authenticate(plaintext))

    def test_wildcard_staff_key_cannot_be_inherited_by_the_survivor(self):
        """Inverts ``test_staff_kind_key_owned_by_secondary_also_transfers``."""
        self.secondary.is_staff = True
        self.secondary.save(update_fields=["is_staff"])
        self.canonical.is_staff = True
        self.canonical.save(update_fields=["is_staff"])
        key, plaintext = APIKey.create_for_user(
            user=self.secondary,
            name="dupe operator key",
            scopes=["*"],
            kind=APIKey.Kind.STAFF,
        )

        merge_accounts(
            self.canonical,
            self.secondary,
            actor_label="test:1736",
            force=True,
        )

        key.refresh_from_db()
        self.assertEqual(key.user_id, self.secondary.pk)
        self.assertIsNotNone(key.revoked_at)
        # allows(("settings.write",)) is unreachable through that secret under
        # any identity, because the secret no longer resolves to a key at all.
        self.assertIsNone(APIKey.authenticate(plaintext))

    def test_merge_plan_reports_the_revocation_and_no_move(self):
        """Inverts ``test_merge_plan_reports_no_credential_revocation_for_package_keys``."""
        APIKey.create_for_user(
            user=self.secondary,
            name="dupe member key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        plan = merge_accounts(
            self.canonical, self.secondary, actor_label="test:1736"
        )
        payload = plan.to_dict()

        self.assertEqual(
            set(payload["credentials"]),
            {
                "member_api_keys_revoked",
                "operator_tokens_deleted",
                "package_api_keys_revoked",
            },
        )
        self.assertEqual(payload["credentials"]["package_api_keys_revoked"], 1)
        # The credential is not presented as something that moves.
        package_moves = [
            entry
            for entry in payload["moved"]
            if entry["model"] == APIKey._meta.label
        ]
        self.assertEqual(package_moves, [])

    def test_staff_key_whose_owner_lost_staff_rights_is_revoked_not_rejected(self):
        """``APIKey.clean()`` must never run during a merge revocation.

        A ``kind="staff"`` row whose owner is no longer ``is_staff`` is a legacy
        state ``clean()`` rejects. The strategy revokes through a queryset
        ``update()``, which bypasses model validation, so the merge completes.
        """
        self.secondary.is_staff = True
        self.secondary.save(update_fields=["is_staff"])
        key, plaintext = APIKey.create_for_user(
            user=self.secondary,
            name="legacy staff key",
            scopes=["*"],
            kind=APIKey.Kind.STAFF,
        )
        self.secondary.is_staff = False
        self.secondary.save(update_fields=["is_staff"])

        plan = merge_accounts(
            self.canonical, self.secondary, actor_label="test:1736"
        )

        self.assertEqual(plan.credentials["package_api_keys_revoked"], 1)
        key.refresh_from_db()
        self.assertIsNotNone(key.revoked_at)
        self.assertIsNone(APIKey.authenticate(plaintext))

    def test_dry_run_reports_the_revocation_and_persists_nothing(self):
        key, plaintext = APIKey.create_for_user(
            user=self.secondary,
            name="dupe member key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        plan = merge_accounts(
            self.canonical,
            self.secondary,
            actor_label="test:1736",
            dry_run=True,
        )

        self.assertEqual(plan.credentials["package_api_keys_revoked"], 1)
        key.refresh_from_db()
        self.assertIsNone(key.revoked_at)
        authenticated = APIKey.authenticate(plaintext)
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, key.pk)

    def test_canonical_keys_survive_and_already_revoked_rows_are_not_restamped(self):
        canonical_key, canonical_plaintext = APIKey.create_for_user(
            user=self.canonical,
            name="survivor key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        already_revoked, _ = APIKey.create_for_user(
            user=self.secondary,
            name="old dupe key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        already_revoked.revoke()
        original_revoked_at = already_revoked.revoked_at
        APIKey.create_for_user(
            user=self.secondary,
            name="live dupe key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        plan = merge_accounts(
            self.canonical, self.secondary, actor_label="test:1736"
        )

        self.assertEqual(plan.credentials["package_api_keys_revoked"], 1)
        canonical_key.refresh_from_db()
        self.assertIsNone(canonical_key.revoked_at)
        self.assertEqual(canonical_key.user_id, self.canonical.pk)
        authenticated = APIKey.authenticate(canonical_plaintext)
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, canonical_key.pk)
        already_revoked.refresh_from_db()
        self.assertEqual(already_revoked.revoked_at, original_revoked_at)

    def test_merge_audit_row_records_the_revoked_count(self):
        APIKey.create_for_user(
            user=self.secondary,
            name="dupe member key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        merge_accounts(
            self.canonical, self.secondary, actor_label="test:1736"
        )

        rows = CommunityAuditLog.objects.filter(action=AUDIT_ACTION)
        self.assertEqual(rows.count(), 1)
        details = json.loads(rows.get().details)
        self.assertEqual(details["credentials"]["package_api_keys_revoked"], 1)


class CredentialStrategyRegistrationGuardTest(TestCase):
    """Structural guard: no credential model may reach the generic repoint.

    A model storing a hashed secret keyed to a user must be handled by an
    explicit strategy. ``key_hash`` is the predicate because it means exactly
    that; ``accounts/services/privacy.py`` already treats the same field name as
    its sensitive-field marker, and it matches the three credential models with
    no false positives (``payments.CheckoutAccountBinding`` carries
    ``revoked_at`` but is legitimately repointable, so ``revoked_at`` would be
    the wrong predicate).
    """

    def test_every_user_owned_credential_model_has_an_explicit_strategy(self):
        unregistered = []
        found = set()
        for field in User._meta.get_fields():
            if not field.auto_created or field.many_to_many:
                continue
            if not (field.one_to_many or field.one_to_one):
                continue
            related_model = field.related_model
            try:
                key_hash = related_model._meta.get_field("key_hash")
            except FieldDoesNotExist:
                continue
            if not key_hash.concrete:
                continue
            pair = (related_model._meta.label, field.remote_field.name)
            found.add(pair)
            if pair not in _SPECIAL_STRATEGIES:
                unregistered.append(pair)

        self.assertEqual(
            unregistered,
            [],
            "Credential models must never be repointed on merge: "
            + ", ".join(
                f"{label}.{field} is not registered in _SPECIAL_STRATEGIES"
                for label, field in unregistered
            ),
        )
        self.assertEqual(
            found,
            {
                ("accounts.Token", "user"),
                ("accounts.MemberAPIKey", "user"),
                (APIKey._meta.label, "user"),
            },
        )

    def test_package_key_is_registered_under_the_models_own_label(self):
        """A package app-label rename must fail loud, not silently repoint."""
        self.assertIn((APIKey._meta.label, "user"), _SPECIAL_STRATEGIES)
        self.assertEqual(APIKey._meta.label, "cb_api.APIKey")
