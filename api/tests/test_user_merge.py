"""Tests for ``POST /api/users/merge`` -- the account-merge engine (issue #841).

Covers every groomed Django scenario, each asserting real DB state so it fails
if the behaviour breaks: simple-FK repoint, tag UNION, ``email_verified`` OR,
higher-``tier`` wins, a ``unique_together`` collision resolving WITHOUT
IntegrityError, the active-enrollment partial-unique collision, the
``UserAttribution`` PK-O2O recreate, the ``CRMRecord`` O2O field-merge,
TierOverride reconciliation, the stefano-shaped redundant-override revoke, dual
live subscriptions (409 without force / success with force), the dry-run no-op,
the audit row + alias on a real merge, self-merge 400, unknown-email 404, strict
keys, staff-merge refusal, the idempotent already-merged no-op, and staff-token
auth (401 unauth + non-staff).
"""

import json
from unittest import mock

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, TierOverride, Token
from analytics.models import UserAttribution
from community.models import CommunityAuditLog
from content.models import Course, Enrollment
from crm.models import CRMRecord
from email_app.models import EmailCampaign, EmailLog
from events.models import Event, EventRegistration
from payments.models import Tier

User = get_user_model()


class UserMergeTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, _ = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0}
        )
        cls.basic, _ = Tier.objects.get_or_create(
            slug="basic", defaults={"name": "Basic", "level": 10}
        )
        cls.main, _ = Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20}
        )
        cls.premium, _ = Tier.objects.get_or_create(
            slug="premium", defaults={"name": "Premium", "level": 30}
        )
        cls.admin = User.objects.create_user(
            email="admin@test.com", password="x", is_staff=True, is_superuser=True
        )
        cls.token = Token.objects.create(user=cls.admin, name="merge-bot")

    def _post(self, payload, *, token=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        headers = {}
        if token is False:
            pass
        else:
            key = token.key if token is not None else self.token.key
            headers["HTTP_AUTHORIZATION"] = f"Token {key}"
        return self.client.post(
            "/api/users/merge",
            data=body,
            content_type="application/json",
            **headers,
        )

    def _make_pair(self, canonical_email="keep@test.com", secondary_email="dupe@test.com"):
        canonical = User.objects.create_user(email=canonical_email, password="x")
        secondary = User.objects.create_user(email=secondary_email, password="x")
        return canonical, secondary


class SimpleFkRepointTest(UserMergeTestBase):
    def test_email_logs_repointed_secondary_to_canonical(self):
        canonical, secondary = self._make_pair()
        for _ in range(3):
            EmailLog.objects.create(user=secondary, email_type="campaign")
        EmailLog.objects.create(user=canonical, email_type="campaign")

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(EmailLog.objects.filter(user=secondary).count(), 0)
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 4)

        moved = response.json()["moved"]
        entry = next(m for m in moved if m["model"] == "email_app.EmailLog")
        self.assertEqual(entry["moved"], 3)


class EmailAddressRepointTest(UserMergeTestBase):
    """allauth ``EmailAddress``: per-user ``primary`` STATE flag must NOT drop.

    Regression guard for the silent-data-loss bug: the generic unique-key walker
    derived ``(primary,) WHERE primary=True`` from
    ``UniqueConstraint(user, primary)`` and DELETED secondary's primary
    verification record on collision. The correct behaviour is REPOINT (demote
    the secondary's ``primary`` to False), preserving the verification record for
    exactly the address being aliased to canonical.
    """

    def test_secondary_primary_email_survives_repointed_and_demoted(self):
        canonical, secondary = self._make_pair()
        EmailAddress.objects.create(
            user=canonical, email="keep@test.com", primary=True, verified=True
        )
        sec_addr = EmailAddress.objects.create(
            user=secondary, email="dupe@test.com", primary=True, verified=True
        )

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        # Secondary's verification record SURVIVES on canonical (not dropped).
        moved = EmailAddress.objects.filter(user=canonical, email="dupe@test.com")
        self.assertEqual(moved.count(), 1, "secondary EmailAddress was silently dropped")
        survivor = moved.get()
        self.assertEqual(survivor.pk, sec_addr.pk)
        self.assertTrue(survivor.verified)
        # Demoted to non-primary so the one-primary-per-user invariant holds.
        self.assertFalse(survivor.primary)
        # Canonical keeps its single primary.
        primaries = EmailAddress.objects.filter(user=canonical, primary=True)
        self.assertEqual(primaries.count(), 1)
        self.assertEqual(primaries.get().email, "keep@test.com")
        # Secondary owns no EmailAddress rows anymore.
        self.assertFalse(EmailAddress.objects.filter(user=secondary).exists())

    def test_duplicate_address_dropped_not_repointed(self):
        # When canonical ALREADY owns the same address, the true (user, email)
        # duplicate on secondary is dropped (no second copy on canonical).
        canonical, secondary = self._make_pair()
        EmailAddress.objects.create(
            user=canonical, email="shared@test.com", primary=False, verified=True
        )
        EmailAddress.objects.create(
            user=secondary, email="shared@test.com", primary=False, verified=False
        )

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(
            EmailAddress.objects.filter(user=canonical, email="shared@test.com").count(),
            1,
        )
        self.assertFalse(EmailAddress.objects.filter(user=secondary).exists())


class EmailLogCampaignRepointTest(UserMergeTestBase):
    """``email_app.EmailLog`` is PLAIN REPOINT -- campaign history is preserved.

    ``EmailLog`` carries ``UniqueConstraint(campaign, user) WHERE campaign IS NOT
    NULL``. The spec table classifies it as plain repoint (preserve ALL delivery
    history). The old ``SimpleFkRepointTest`` masked the bug by using
    ``campaign=NULL`` so the partial condition never fired. Here a REAL campaign
    FK is present on BOTH accounts (the partial condition fires) and every log
    survives on canonical -- the generic drop-walker no longer deletes campaign
    history.

    Note: the DB unique index forbids two ``(same campaign, same user)`` rows, so
    "history preserved" means each distinct campaign-send record is repointed,
    not dropped. We give each account its own distinct campaign so both real
    delivery records survive on canonical.
    """

    def test_campaign_logs_on_both_sides_repointed_not_dropped(self):
        canonical, secondary = self._make_pair()
        campaign_a = EmailCampaign.objects.create(subject="Promo A", body="hi")
        campaign_b = EmailCampaign.objects.create(subject="Promo B", body="yo")
        canon_log = EmailLog.objects.create(
            user=canonical, campaign=campaign_a, email_type="campaign"
        )
        sec_log = EmailLog.objects.create(
            user=secondary, campaign=campaign_b, email_type="campaign"
        )

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        # Both real campaign-send records survive on canonical -- none dropped.
        logs = EmailLog.objects.filter(user=canonical)
        self.assertEqual(logs.count(), 2, "a campaign log was silently dropped")
        self.assertEqual(
            set(logs.values_list("pk", flat=True)), {canon_log.pk, sec_log.pk}
        )
        self.assertEqual(
            EmailLog.objects.filter(user=canonical, campaign=campaign_b).count(), 1
        )
        self.assertFalse(EmailLog.objects.filter(user=secondary).exists())

        entry = next(
            m
            for m in response.json()["moved"]
            if m["model"] == "email_app.EmailLog"
        )
        # Plain repoint records ``moved`` only -- no ``dropped`` key at all.
        self.assertEqual(entry["moved"], 1)
        self.assertNotIn("dropped", entry)


class AtomicityTest(UserMergeTestBase):
    """A failure mid-merge rolls the WHOLE thing back -- nothing half-applied."""

    def test_failure_mid_merge_rolls_everything_back(self):
        canonical, secondary = self._make_pair()
        EmailLog.objects.create(user=secondary, email_type="campaign")
        secondary.tags = ["x"]
        secondary.save(update_fields=["tags"])

        # Inject a failure AFTER the repoint + scalar passes have already written
        # to the real DB, so we prove those writes are rolled back too.
        with mock.patch(
            "accounts.services.account_merge._reconcile_tier_overrides",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                from accounts.services.account_merge import merge_accounts

                merge_accounts(
                    canonical, secondary, actor_label="atomicity-test"
                )

        # EVERYTHING untouched: rows still on secondary, no alias, secondary
        # still active, no audit row, canonical's tags unchanged.
        self.assertEqual(EmailLog.objects.filter(user=secondary).count(), 1)
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 0)
        self.assertFalse(EmailAlias.objects.filter(user=canonical).exists())
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)
        canonical.refresh_from_db()
        self.assertEqual(canonical.tags, [])
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )


class ScalarReconcileTest(UserMergeTestBase):
    def test_tags_unioned_without_duplicates(self):
        canonical, secondary = self._make_pair()
        canonical.tags = ["a", "b"]
        canonical.save(update_fields=["tags"])
        secondary.tags = ["b", "c"]
        secondary.save(update_fields=["tags"])

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        canonical.refresh_from_db()
        self.assertEqual(canonical.tags, ["a", "b", "c"])
        self.assertEqual(response.json()["reconciled"]["tags"]["added"], ["c"])

    def test_email_verified_is_ord(self):
        canonical, secondary = self._make_pair()
        canonical.email_verified = False
        canonical.save(update_fields=["email_verified"])
        secondary.email_verified = True
        secondary.save(update_fields=["email_verified"])

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200)

        canonical.refresh_from_db()
        self.assertTrue(canonical.email_verified)

    def test_tier_resolves_to_higher_level(self):
        canonical, secondary = self._make_pair()
        canonical.tier = self.free
        canonical.save(update_fields=["tier"])
        secondary.tier = self.main
        secondary.save(update_fields=["tier"])

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200)

        canonical.refresh_from_db()
        self.assertEqual(canonical.tier.slug, "main")
        self.assertEqual(response.json()["reconciled"]["tier"]["to"], "main")


class UniqueTogetherCollisionTest(UserMergeTestBase):
    def test_event_registration_collision_keeps_canonical_drops_secondary(self):
        canonical, secondary = self._make_pair()
        event_e = Event.objects.create(
            slug="event-e", title="E", start_datetime=timezone.now()
        )
        event_f = Event.objects.create(
            slug="event-f", title="F", start_datetime=timezone.now()
        )
        canon_reg = EventRegistration.objects.create(event=event_e, user=canonical)
        EventRegistration.objects.create(event=event_e, user=secondary)
        EventRegistration.objects.create(event=event_f, user=secondary)

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        # Exactly canonical's own registration for E survives.
        e_regs = EventRegistration.objects.filter(event=event_e, user=canonical)
        self.assertEqual(e_regs.count(), 1)
        self.assertEqual(e_regs.get().pk, canon_reg.pk)
        # F was repointed.
        self.assertTrue(
            EventRegistration.objects.filter(event=event_f, user=canonical).exists()
        )
        self.assertFalse(EventRegistration.objects.filter(user=secondary).exists())

        entry = next(
            m
            for m in response.json()["moved"]
            if m["model"] == "events.EventRegistration"
        )
        self.assertEqual(entry["moved"], 1)
        self.assertEqual(entry["dropped"], 1)


class EnrollmentPartialUniqueTest(UserMergeTestBase):
    def test_active_enrollment_collision_does_not_crash(self):
        canonical, secondary = self._make_pair()
        course_c = Course.objects.create(slug="course-c", title="C")
        course_d = Course.objects.create(slug="course-d", title="D")
        Enrollment.objects.create(user=canonical, course=course_c)
        Enrollment.objects.create(user=secondary, course=course_c)
        Enrollment.objects.create(user=secondary, course=course_d)

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        # Canonical has exactly one ACTIVE enrollment for C and one for D.
        self.assertEqual(
            Enrollment.objects.filter(
                user=canonical, course=course_c, unenrolled_at__isnull=True
            ).count(),
            1,
        )
        self.assertEqual(
            Enrollment.objects.filter(
                user=canonical, course=course_d, unenrolled_at__isnull=True
            ).count(),
            1,
        )
        # No active enrollment remains on secondary.
        self.assertFalse(
            Enrollment.objects.filter(
                user=secondary, unenrolled_at__isnull=True
            ).exists()
        )


class UserAttributionO2OTest(UserMergeTestBase):
    def test_pk_o2o_collision_keeps_canonical(self):
        canonical, secondary = self._make_pair()
        # UserAttribution is auto-created by a post_save signal, so both exist.
        UserAttribution.objects.update_or_create(
            user=canonical, defaults={"first_touch_utm_source": "canon"}
        )
        UserAttribution.objects.update_or_create(
            user=secondary, defaults={"first_touch_utm_source": "sec"}
        )

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(UserAttribution.objects.filter(pk=canonical.pk).count(), 1)
        self.assertFalse(UserAttribution.objects.filter(pk=secondary.pk).exists())
        self.assertEqual(
            UserAttribution.objects.get(pk=canonical.pk).first_touch_utm_source,
            "canon",
        )


class CRMRecordO2OTest(UserMergeTestBase):
    def test_field_merge_fills_canonical_blanks(self):
        canonical, secondary = self._make_pair()
        CRMRecord.objects.create(user=canonical, summary="")
        CRMRecord.objects.create(user=secondary, summary="from-secondary")

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(CRMRecord.objects.filter(user=canonical).count(), 1)
        self.assertFalse(CRMRecord.objects.filter(user=secondary).exists())
        self.assertEqual(
            CRMRecord.objects.get(user=canonical).summary, "from-secondary"
        )


class TierOverrideReconcileTest(UserMergeTestBase):
    def _override(self, user, tier):
        return TierOverride.objects.create(
            user=user,
            override_tier=tier,
            expires_at=timezone.now() + timezone.timedelta(days=365),
            is_active=True,
        )

    def test_reconciles_to_one_active_higher_wins(self):
        canonical, secondary = self._make_pair()
        main_ov = self._override(canonical, self.main)
        self._override(secondary, self.premium)

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        active = TierOverride.objects.filter(user=canonical, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().override_tier.slug, "premium")
        main_ov.refresh_from_db()
        self.assertFalse(main_ov.is_active)

        deactivated_ids = [
            d["id"] for d in response.json()["tier_overrides"]["deactivated"]
        ]
        self.assertIn(main_ov.id, deactivated_ids)


class StefanoRedundantOverrideTest(UserMergeTestBase):
    def test_paid_via_moved_subscription_revokes_redundant_override(self):
        canonical, secondary = self._make_pair()
        canonical.tier = self.free
        canonical.save(update_fields=["tier"])
        courtesy = TierOverride.objects.create(
            user=canonical,
            override_tier=self.main,
            expires_at=timezone.now() + timezone.timedelta(days=365),
            is_active=True,
        )
        secondary.tier = self.main
        secondary.subscription_id = "sub_live_123"
        secondary.stripe_customer_id = "cus_123"
        secondary.billing_period_end = timezone.now() + timezone.timedelta(days=30)
        secondary.save(
            update_fields=[
                "tier",
                "subscription_id",
                "stripe_customer_id",
                "billing_period_end",
            ]
        )

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        canonical.refresh_from_db()
        self.assertEqual(canonical.tier.slug, "main")
        self.assertEqual(canonical.subscription_id, "sub_live_123")
        self.assertEqual(canonical.stripe_customer_id, "cus_123")
        courtesy.refresh_from_db()
        self.assertFalse(courtesy.is_active)

        deactivated = response.json()["tier_overrides"]["deactivated"]
        self.assertTrue(
            any(d["reason"] == "redundant_after_paid" for d in deactivated)
        )


class DualSubscriptionTest(UserMergeTestBase):
    def _make_dual(self):
        canonical, secondary = self._make_pair()
        canonical.subscription_id = "sub_A"
        canonical.save(update_fields=["subscription_id"])
        secondary.subscription_id = "sub_B"
        secondary.save(update_fields=["subscription_id"])
        EmailLog.objects.create(user=secondary, email_type="campaign")
        return canonical, secondary

    def test_dual_subscription_refused_without_force(self):
        canonical, secondary = self._make_dual()
        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "subscription_conflict")

        # Nothing moved; both subs intact; no audit row.
        self.assertEqual(EmailLog.objects.filter(user=secondary).count(), 1)
        canonical.refresh_from_db()
        secondary.refresh_from_db()
        self.assertEqual(canonical.subscription_id, "sub_A")
        self.assertEqual(secondary.subscription_id, "sub_B")
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )

    def test_dual_subscription_succeeds_with_force(self):
        canonical, secondary = self._make_dual()
        response = self._post(
            {
                "canonical_email": "keep@test.com",
                "merge_email": "dupe@test.com",
                "force": True,
            }
        )
        self.assertEqual(response.status_code, 200, response.content)

        canonical.refresh_from_db()
        self.assertEqual(canonical.subscription_id, "sub_A")
        conflicts = response.json()["conflicts"]
        self.assertTrue(
            any(c.get("dropped_subscription_id") == "sub_B" for c in conflicts)
        )
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 1)


class DryRunTest(UserMergeTestBase):
    def test_dry_run_returns_plan_but_mutates_nothing(self):
        canonical, secondary = self._make_pair()
        for _ in range(2):
            EmailLog.objects.create(user=secondary, email_type="campaign")
        secondary.tags = ["x"]
        secondary.save(update_fields=["tags"])

        response = self._post(
            {
                "canonical_email": "keep@test.com",
                "merge_email": "dupe@test.com",
                "dry_run": True,
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertTrue(
            any(m["model"] == "email_app.EmailLog" for m in body["moved"])
        )

        # NOTHING persisted.
        self.assertEqual(EmailLog.objects.filter(user=secondary).count(), 2)
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 0)
        self.assertFalse(EmailAlias.objects.filter(user=canonical).exists())
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)
        canonical.refresh_from_db()
        self.assertEqual(canonical.tags, [])
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )


class RealMergeAuditAliasTest(UserMergeTestBase):
    def test_real_merge_writes_one_audit_row_and_alias(self):
        canonical, secondary = self._make_pair()

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 200, response.content)

        rows = CommunityAuditLog.objects.filter(action="merge_accounts")
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.user, canonical)
        self.assertIn("merge-bot", row.details)

        alias = EmailAlias.objects.get(user=canonical, email="dupe@test.com")
        self.assertEqual(alias.source, EmailAlias.SOURCE_MERGE)

        secondary.refresh_from_db()
        self.assertFalse(secondary.is_active)
        self.assertEqual(response.json()["alias_created"], "dupe@test.com")

    def test_future_payment_routes_to_canonical_via_resolver(self):
        from accounts.services.email_resolution import resolve_user_by_email

        canonical, secondary = self._make_pair()
        self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        # A future Stripe event for the merged email resolves to canonical.
        resolved = resolve_user_by_email("dupe@test.com")
        self.assertEqual(resolved, canonical)


class SelfMergeTest(UserMergeTestBase):
    def test_self_merge_rejected_case_insensitive(self):
        User.objects.create_user(email="solo@test.com", password="x")
        response = self._post(
            {"canonical_email": "solo@test.com", "merge_email": "SOLO@test.com"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "self_merge")
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )


class UnknownEmailTest(UserMergeTestBase):
    def test_unknown_merge_email_returns_404(self):
        User.objects.create_user(email="keep@test.com", password="x")
        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "ghost@test.com"}
        )
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], "user_not_found")
        self.assertEqual(body["details"]["field"], "merge_email")


class StrictBodyTest(UserMergeTestBase):
    def test_unknown_field_returns_422(self):
        self._make_pair()
        response = self._post(
            {
                "canonical_email": "keep@test.com",
                "merge_email": "dupe@test.com",
                "foo": "bar",
            }
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_field")

    def test_missing_email_returns_422(self):
        response = self._post({"canonical_email": "keep@test.com"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")


class StaffMergeTest(UserMergeTestBase):
    def test_staff_canonical_refused_without_force(self):
        canonical = User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True
        )
        User.objects.create_user(email="dupe@test.com", password="x")
        response = self._post(
            {"canonical_email": "staff@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "staff_merge_refused")
        canonical.refresh_from_db()
        self.assertTrue(canonical.is_active)

    def test_staff_canonical_proceeds_with_force(self):
        User.objects.create_user(
            email="staff@test.com", password="x", is_staff=True
        )
        secondary = User.objects.create_user(email="dupe@test.com", password="x")
        response = self._post(
            {
                "canonical_email": "staff@test.com",
                "merge_email": "dupe@test.com",
                "force": True,
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        secondary.refresh_from_db()
        self.assertFalse(secondary.is_active)


class IdempotentNoOpTest(UserMergeTestBase):
    def test_rerun_already_merged_pair_is_clean_no_op(self):
        canonical, secondary = self._make_pair()
        first = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            CommunityAuditLog.objects.filter(action="merge_accounts").count(), 1
        )

        second = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"}
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["already_merged"])
        # No new audit row.
        self.assertEqual(
            CommunityAuditLog.objects.filter(action="merge_accounts").count(), 1
        )


class AuthTest(UserMergeTestBase):
    def test_no_token_returns_401(self):
        self._make_pair()
        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"},
            token=False,
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )

    def test_non_staff_token_returns_401(self):
        canonical, secondary = self._make_pair()
        # Token requires a staff owner at creation; the request-time gate in
        # ``token_required`` still 401s once the owner loses staff. Create the
        # token staff, then demote the owner.
        owner = User.objects.create_user(
            email="plain@test.com", password="x", is_staff=True
        )
        bad_token = Token.objects.create(user=owner, name="plain")
        owner.is_staff = False
        owner.save(update_fields=["is_staff"])
        EmailLog.objects.create(user=secondary, email_type="campaign")

        response = self._post(
            {"canonical_email": "keep@test.com", "merge_email": "dupe@test.com"},
            token=bad_token,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(EmailLog.objects.filter(user=secondary).count(), 1)
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)
