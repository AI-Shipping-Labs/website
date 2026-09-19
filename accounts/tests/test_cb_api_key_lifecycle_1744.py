"""Deactivation and the GDPR export both know about ``cb_api.APIKey`` (#1744).

Two account-lifecycle services used to ignore ``community_base.api.APIKey``.
Both gaps were confirmed by executing the real services during #1736, not by
reading code:

- ``accounts/services/credentials.py::revoke_api_credentials_on_deactivation``
  handled ``accounts.MemberAPIKey`` and ``accounts.Token`` only. After
  deactivation the package key's ``revoked_at`` stayed ``NULL``;
  ``APIKey.authenticate()`` returned ``None`` only because the package filters
  ``user__is_active=True``, and the very same plaintext authenticated again the
  moment the account was reactivated. Suppression is not revocation, and the
  site must not depend on a package internal that a pin bump could relax.
- ``accounts/services/privacy.py::_auth_security`` named five entries and none
  of them was the package key, so a subject-access export was incomplete.

This file pins the fixed behaviour the way ``test_merge_cb_api_key_1736.py``
does: real rows minted through ``APIKey.create_for_user``, real service calls,
and assertions on executed behaviour rather than on the shape of the code.
``APIKey.authenticate(plaintext) is None`` is the only available statement of
"the key stops working" -- this site mounts no ``/api/v1/`` routes today.

The last guard is structural: every user-owned model with a concrete
``key_hash`` field must be named by BOTH services, so the next credential model
mounted onto the site fails a test instead of shipping a hole.
"""

import json

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.test import Client, TestCase
from django.utils import timezone

from accounts.services.account_merge import merge_accounts
from accounts.services.credentials import DEACTIVATION_CREDENTIAL_HANDLERS
from accounts.services.privacy import (
    CREDENTIAL_EXPORT_SECTIONS,
    SCHEMA_VERSION,
    build_user_data_export,
)
from payments.models import Tier

User = get_user_model()


def _user_owned_credential_models():
    """``(model label, user field name)`` for every user-owned credential model.

    A concrete ``key_hash`` field is the predicate: it means "stores a hashed
    secret keyed to a user". ``privacy.py`` already treats the same field name
    as its sensitive-field marker, and it matches exactly the three credential
    models with no false positives.
    """
    pairs = set()
    for field in User._meta.get_fields():
        if not field.auto_created or field.many_to_many:
            continue
        if not (field.one_to_many or field.one_to_one):
            continue
        try:
            key_hash = field.related_model._meta.get_field("key_hash")
        except FieldDoesNotExist:
            continue
        if not key_hash.concrete:
            continue
        pairs.add((field.related_model._meta.label, field.remote_field.name))
    return pairs


def _credential_models_missing_from_services(handlers, export_sections):
    """Report credential models absent from either lifecycle enumeration."""
    exported_labels = {f"{app}.{name}" for app, name in export_sections}
    missing = []
    for label, field_name in sorted(_user_owned_credential_models()):
        gaps = []
        if (label, field_name) not in handlers:
            gaps.append(
                "accounts.services.credentials.DEACTIVATION_CREDENTIAL_HANDLERS"
            )
        if label not in exported_labels:
            gaps.append("accounts.services.privacy.CREDENTIAL_EXPORT_SECTIONS")
        if gaps:
            missing.append((label, gaps))
    return missing


class DeactivationRevokesPackageApiKeysTest(TestCase):
    """Execute real deactivations against real ``cb_api.APIKey`` rows."""

    def test_deactivated_operator_cannot_be_revived_into_a_live_api_key(self):
        operator = User.objects.create_user(
            email="operator-1744@test.com",
            password="pw",
            is_staff=True,
        )
        staff_key, staff_plaintext = APIKey.create_for_user(
            user=operator,
            name="wildcard automation",
            scopes=["*"],
            kind=APIKey.Kind.STAFF,
        )
        member_key, member_plaintext = APIKey.create_for_user(
            user=operator,
            name="personal key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        self.assertIsNotNone(APIKey.authenticate(staff_plaintext))
        self.assertIsNotNone(APIKey.authenticate(member_plaintext))

        operator.is_active = False
        operator.save(update_fields=["is_active"])

        staff_key.refresh_from_db()
        member_key.refresh_from_db()
        self.assertIsNotNone(staff_key.revoked_at)
        self.assertIsNotNone(member_key.revoked_at)
        self.assertIsNone(APIKey.authenticate(staff_plaintext))
        self.assertIsNone(APIKey.authenticate(member_plaintext))

        staff_revoked_at = staff_key.revoked_at
        member_revoked_at = member_key.revoked_at

        # Reactivation restores nothing: the rows stay revoked, so the
        # package's user__is_active filter is no longer what protects us.
        operator.is_active = True
        operator.save(update_fields=["is_active"])

        staff_key.refresh_from_db()
        member_key.refresh_from_db()
        self.assertEqual(staff_key.revoked_at, staff_revoked_at)
        self.assertEqual(member_key.revoked_at, member_revoked_at)
        self.assertIsNone(APIKey.authenticate(staff_plaintext))
        self.assertIsNone(APIKey.authenticate(member_plaintext))

    def test_demoted_operators_legacy_staff_key_is_revoked_not_rejected(self):
        """``APIKey.clean()`` must never run inside ``User.save()``.

        ``clean()`` rejects a ``kind="staff"`` row whose owner is not
        ``is_staff``. Revoking through a queryset ``update()`` bypasses model
        validation, so the deactivation of a demoted operator completes instead
        of raising ``ValidationError`` out of ``User.save()``.
        """
        user = User.objects.create_user(
            email="demoted-1744@test.com",
            password="pw",
            is_staff=True,
        )
        key, plaintext = APIKey.create_for_user(
            user=user,
            name="legacy staff key",
            scopes=["*"],
            kind=APIKey.Kind.STAFF,
        )
        user.is_staff = False
        user.save(update_fields=["is_staff"])

        user.is_active = False
        user.save(update_fields=["is_active"])

        user.refresh_from_db()
        self.assertFalse(user.is_active)
        key.refresh_from_db()
        self.assertIsNotNone(key.revoked_at)
        self.assertIsNone(APIKey.authenticate(plaintext))

    def test_one_members_deactivation_leaves_everyone_else_alone(self):
        member_a = User.objects.create_user(email="a-1744@test.com", password="pw")
        member_b = User.objects.create_user(email="b-1744@test.com", password="pw")
        live_key, live_plaintext = APIKey.create_for_user(
            user=member_a,
            name="a live key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        old_key, old_plaintext = APIKey.create_for_user(
            user=member_a,
            name="a old key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        old_revoked_at = timezone.now() - timezone.timedelta(days=7)
        APIKey.objects.filter(pk=old_key.pk).update(revoked_at=old_revoked_at)
        other_key, other_plaintext = APIKey.create_for_user(
            user=member_b,
            name="b live key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        member_a.is_active = False
        member_a.save(update_fields=["is_active"])

        live_key.refresh_from_db()
        old_key.refresh_from_db()
        other_key.refresh_from_db()
        self.assertIsNotNone(live_key.revoked_at)
        self.assertIsNone(APIKey.authenticate(live_plaintext))
        # Already-revoked rows keep their original timestamp.
        self.assertEqual(old_key.revoked_at, old_revoked_at)
        self.assertIsNone(APIKey.authenticate(old_plaintext))
        self.assertIsNone(other_key.revoked_at)
        authenticated = APIKey.authenticate(other_plaintext)
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, other_key.pk)

    def test_ordinary_profile_save_does_not_revoke_a_members_key(self):
        member = User.objects.create_user(
            email="ordinary-1744@test.com", password="pw"
        )
        key, plaintext = APIKey.create_for_user(
            user=member,
            name="untouched key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        member.unsubscribed = True
        member.save(update_fields=["unsubscribed"])

        key.refresh_from_db()
        self.assertIsNone(key.revoked_at)
        authenticated = APIKey.authenticate(plaintext)
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, key.pk)


class StudioApiKeyListShowsDeactivatedOwnersKeyAsRevokedTest(TestCase):
    """An operator auditing ``/studio/api-keys/`` sees the revocation."""

    def test_status_cell_reads_revoked_after_the_owner_is_deactivated(self):
        superuser = User.objects.create_user(
            email="super-1744@test.com",
            password="pw",
            is_staff=True,
            is_superuser=True,
        )
        owner = User.objects.create_user(
            email="owner-1744@test.com",
            password="pw",
            is_staff=True,
        )
        APIKey.create_for_user(
            user=owner,
            name="deactivated owner key",
            scopes=["settings.read"],
            kind=APIKey.Kind.STAFF,
        )
        client = Client()
        self.assertTrue(
            client.login(email=superuser.email, password="pw")
        )

        before = client.get("/studio/api-keys/")
        self.assertIn("Active", self._status_cell(before))

        owner.is_active = False
        owner.save(update_fields=["is_active"])

        after = client.get("/studio/api-keys/")
        status_cell = self._status_cell(after)
        self.assertIn("Revoked", status_cell)
        self.assertNotIn("Active", status_cell)

    def _status_cell(self, response):
        marker = 'data-testid="api-key-status"'
        self.assertContains(response, marker)
        body = response.content.decode()
        start = body.index(marker)
        return body[start : body.index("</td>", start)]


class PackageApiKeyDataExportTest(TestCase):
    """The subject-access export lists the package keys, never the secrets."""

    def test_member_sees_the_package_keys_they_hold_with_no_secrets(self):
        member = User.objects.create_user(
            email="export-1744@test.com", password="pw"
        )
        live_key, live_plaintext = APIKey.create_for_user(
            user=member,
            name="live package key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        revoked_key, revoked_plaintext = APIKey.create_for_user(
            user=member,
            name="retired package key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        revoked_at = timezone.now() - timezone.timedelta(days=3)
        APIKey.objects.filter(pk=revoked_key.pk).update(revoked_at=revoked_at)

        payload = build_user_data_export(member)
        auth_security = payload["auth_security"]

        self.assertEqual(
            set(auth_security),
            {
                "allauth_email_addresses",
                "email_aliases",
                "member_api_keys",
                "oauth_social_accounts",
                "package_api_keys",
                "staff_api_tokens",
            },
        )
        rows = {row["id"]: row for row in auth_security["package_api_keys"]}
        self.assertEqual(set(rows), {live_key.pk, revoked_key.pk})
        for row in rows.values():
            self.assertEqual(
                set(row),
                {
                    "id",
                    "name",
                    "kind",
                    "lookup_prefix",
                    "scopes",
                    "created_at",
                    "revoked_at",
                    "last_used_at",
                },
            )
        live_row = rows[live_key.pk]
        self.assertEqual(live_row["name"], "live package key")
        self.assertEqual(live_row["kind"], APIKey.Kind.MEMBER)
        self.assertEqual(live_row["lookup_prefix"], live_key.lookup_prefix)
        self.assertEqual(live_row["scopes"], ["users.read"])
        self.assertIsNone(live_row["revoked_at"])
        self.assertEqual(
            rows[revoked_key.pk]["revoked_at"], revoked_at.isoformat()
        )

        serialized = json.dumps(payload)
        self.assertNotIn(live_plaintext, serialized)
        self.assertNotIn(revoked_plaintext, serialized)
        self.assertNotIn(live_key.key_hash, serialized)
        self.assertNotIn(revoked_key.key_hash, serialized)
        self.assertNotIn("key_hash", serialized)
        self.assertNotIn("last_used_ip_hash", serialized)

    def test_staff_kind_keys_are_exported_to_their_owner_too(self):
        operator = User.objects.create_user(
            email="operator-export-1744@test.com",
            password="pw",
            is_staff=True,
        )
        APIKey.create_for_user(
            user=operator,
            name="automation key",
            scopes=["settings.read"],
            kind=APIKey.Kind.STAFF,
        )

        rows = build_user_data_export(operator)["auth_security"][
            "package_api_keys"
        ]

        self.assertEqual([row["kind"] for row in rows], [APIKey.Kind.STAFF])

    def test_member_without_keys_downloads_a_complete_valid_export(self):
        member = User.objects.create_user(
            email="empty-1744@test.com", password="pw"
        )
        self.client.force_login(member)

        response = self.client.get("/account/api/data-export")

        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn(
            'attachment; filename="ai-shipping-labs-data-',
            response["Content-Disposition"],
        )
        payload = json.loads(response.content)
        self.assertEqual(payload["auth_security"]["package_api_keys"], [])
        self.assertEqual(payload["auth_security"]["member_api_keys"], [])
        self.assertEqual(payload["manifest"]["schema_version"], SCHEMA_VERSION)
        # The export builder owns the plain-value contract: the whole payload
        # must serialize with no ``default=`` fallback, exactly as
        # ``data_export_view`` calls it.
        json.dumps(payload)


class CredentialLifecycleEnumerationGuardTest(TestCase):
    """The "which services enumerate credentials" answer must stay true."""

    def test_every_user_owned_credential_model_is_named_by_both_services(self):
        missing = _credential_models_missing_from_services(
            DEACTIVATION_CREDENTIAL_HANDLERS,
            CREDENTIAL_EXPORT_SECTIONS,
        )

        self.assertEqual(
            missing,
            [],
            "Credential models must be handled by deactivation AND exported: "
            + "; ".join(
                f"{label} is missing from {' and '.join(gaps)}"
                for label, gaps in missing
            ),
        )
        # A package app-label rename must fail loud here rather than quietly
        # dropping the model out of both services.
        self.assertIn(
            ("cb_api.APIKey", "user"), _user_owned_credential_models()
        )

    def test_guard_fails_when_the_package_key_is_dropped_from_deactivation(self):
        mutated = {
            key: handler
            for key, handler in DEACTIVATION_CREDENTIAL_HANDLERS.items()
            if key != ("cb_api.APIKey", "user")
        }

        missing = _credential_models_missing_from_services(
            mutated, CREDENTIAL_EXPORT_SECTIONS
        )

        self.assertEqual(
            missing,
            [
                (
                    "cb_api.APIKey",
                    [
                        "accounts.services.credentials."
                        "DEACTIVATION_CREDENTIAL_HANDLERS"
                    ],
                )
            ],
        )

    def test_guard_fails_when_the_package_key_is_dropped_from_the_export(self):
        mutated = {
            key: spec
            for key, spec in CREDENTIAL_EXPORT_SECTIONS.items()
            if key != ("cb_api", "APIKey")
        }

        missing = _credential_models_missing_from_services(
            DEACTIVATION_CREDENTIAL_HANDLERS, mutated
        )

        self.assertEqual(
            missing,
            [
                (
                    "cb_api.APIKey",
                    ["accounts.services.privacy.CREDENTIAL_EXPORT_SECTIONS"],
                )
            ],
        )


class MergeStillReportsRevokedPackageKeysTest(TestCase):
    """The new deactivation hook must not pre-empt #1736's counter.

    ``merge_accounts`` runs ``_repoint_relations`` (and therefore
    ``_strategy_package_api_key``) BEFORE ``secondary.save(is_active=False)``.
    That ordering is load-bearing: if it inverted, the deactivation hook would
    revoke the rows first and the merge report would say 0 package keys were
    revoked while a live credential silently changed hands.
    """

    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0}
        )

    def test_merge_counts_the_secondarys_live_package_keys(self):
        canonical = User.objects.create_user(
            email="keep-1744@test.com", password="pw"
        )
        secondary = User.objects.create_user(
            email="dupe-1744@test.com", password="pw"
        )
        key, plaintext = APIKey.create_for_user(
            user=secondary,
            name="dupe package key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

        plan = merge_accounts(canonical, secondary, actor_label="test:1744")

        self.assertEqual(plan.credentials["package_api_keys_revoked"], 1)
        key.refresh_from_db()
        self.assertEqual(key.user_id, secondary.pk)
        self.assertIsNotNone(key.revoked_at)
        self.assertIsNone(APIKey.authenticate(plaintext))
