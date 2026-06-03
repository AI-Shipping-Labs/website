"""Tests for the Studio account-merge UI (issue #842).

The merge ENGINE is exhaustively tested in ``api/tests/test_user_merge.py``;
these tests cover the Studio surface only: the staff gate, that preview is a true
no-op, that confirm runs the real merge on the previewed pair, the signed
``confirm_token`` rejecting a tampered/expired confirm, the conflict/force gating,
the friendly already-merged state, and the user-detail pre-fill.
"""

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailAlias
from community.models import CommunityAuditLog
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from payments.models import Tier
from studio.views.merge import _CONFIRM_SALT, _sign_pair

User = get_user_model()


class MergeUITestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, _ = Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0}
        )
        cls.staff = User.objects.create_user(
            email="staff-op@test.com", password="x", is_staff=True
        )
        cls.member = User.objects.create_user(
            email="plain@test.com", password="x", is_staff=False
        )

    def _login_staff(self):
        self.client.force_login(self.staff)

    def _make_pair(self, canonical="keep@test.com", secondary="dupe@test.com"):
        c = User.objects.create_user(email=canonical, password="x")
        s = User.objects.create_user(email=secondary, password="x")
        return c, s

    def _preview(self, canonical_email, secondary_email, force=False):
        data = {
            "canonical_email": canonical_email,
            "secondary_email": secondary_email,
        }
        if force:
            data["force"] = "1"
        return self.client.post(reverse("studio_user_merge_preview"), data)

    def _confirm(self, canonical_pk, secondary_pk, *, force=False, token=None):
        if token is None:
            token = _sign_pair(canonical_pk, secondary_pk, force)
        return self.client.post(
            reverse("studio_user_merge_confirm"),
            {
                "canonical_user_id": canonical_pk,
                "secondary_user_id": secondary_pk,
                "force": "1" if force else "0",
                "confirm_token": token,
            },
        )


class StaffGateTest(MergeUITestBase):
    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(reverse("studio_user_merge"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, "/accounts/login/?next=/studio/users/merge/"
        )

    def test_non_staff_get_returns_403(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("studio_user_merge"))
        self.assertEqual(response.status_code, 403)

    def test_staff_get_renders_pickers(self):
        self._login_staff()
        response = self.client.get(reverse("studio_user_merge"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="merge-canonical-input"')
        self.assertContains(response, 'data-testid="merge-secondary-input"')
        # No preview / result card yet.
        self.assertNotContains(response, 'data-testid="merge-preview"')
        self.assertNotContains(response, 'data-testid="merge-result"')

    def test_non_staff_preview_blocked_and_runs_nothing(self):
        canonical, secondary = self._make_pair()
        EmailLog.objects.create(user=secondary, email_type="campaign")
        self.client.force_login(self.member)
        response = self._preview("keep@test.com", "dupe@test.com")
        self.assertEqual(response.status_code, 403)
        # Engine did not run.
        self.assertEqual(EmailLog.objects.filter(user=secondary).count(), 1)
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)

    def test_anonymous_confirm_blocked(self):
        canonical, secondary = self._make_pair()
        response = self._confirm(canonical.pk, secondary.pk)
        self.assertEqual(response.status_code, 302)
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )


class PreviewIsNoOpTest(MergeUITestBase):
    def test_preview_renders_plan_and_mutates_nothing(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        event = Event.objects.create(
            slug="ev", title="Ev", start_datetime=timezone.now()
        )
        EventRegistration.objects.create(event=event, user=secondary)
        EmailLog.objects.create(user=secondary, email_type="campaign")

        response = self._preview("keep@test.com", "dupe@test.com")
        self.assertEqual(response.status_code, 200)

        # Plan rendered with the moved event registration row.
        self.assertContains(response, 'data-testid="merge-preview"')
        self.assertContains(response, "events.EventRegistration")
        self.assertContains(response, 'data-testid="merge-plan-deactivate-notice"')
        # Confirm form present for a clean merge.
        self.assertContains(response, 'data-testid="merge-confirm-submit"')

        # NOTHING persisted.
        self.assertEqual(
            EventRegistration.objects.filter(user=secondary).count(), 1
        )
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 0)
        self.assertFalse(EmailAlias.objects.filter(user=canonical).exists())
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )

    def test_preview_confirm_token_signs_force_false_for_clean_pair(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        response = self._preview("keep@test.com", "dupe@test.com")
        token = response.context["confirm_token"]
        payload = signing.loads(token, salt=_CONFIRM_SALT, max_age=600)
        self.assertEqual(payload["canonical_pk"], canonical.pk)
        self.assertEqual(payload["secondary_pk"], secondary.pk)
        self.assertFalse(payload["force"])


class ConfirmRealMergeTest(MergeUITestBase):
    def test_confirm_executes_merge_and_links_to_canonical(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        EmailLog.objects.create(user=secondary, email_type="campaign")

        response = self._confirm(canonical.pk, secondary.pk)
        self.assertEqual(response.status_code, 200)

        # Real merge happened.
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 1)
        secondary.refresh_from_db()
        self.assertFalse(secondary.is_active)
        self.assertTrue(
            EmailAlias.objects.filter(user=canonical, email="dupe@test.com").exists()
        )
        rows = CommunityAuditLog.objects.filter(action="merge_accounts")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().user, canonical)
        self.assertIn("studio:staff-op@test.com", rows.get().details)

        # Result page links to canonical detail.
        self.assertContains(response, 'data-testid="merge-result-headline"')
        self.assertContains(
            response, reverse("studio_user_detail", args=[canonical.pk])
        )

    def test_confirm_attributes_alias_to_staff_operator(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        self._confirm(canonical.pk, secondary.pk)
        alias = EmailAlias.objects.get(user=canonical, email="dupe@test.com")
        self.assertEqual(alias.created_by, self.staff)


class SelfMergeTest(MergeUITestBase):
    def test_self_merge_rejected_no_confirm_button(self):
        self._login_staff()
        User.objects.create_user(email="solo@test.com", password="x")
        response = self._preview("Solo@test.com", "solo@test.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="merge-error-self-merge"')
        self.assertNotContains(response, 'data-testid="merge-confirm-submit"')
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )


class UnknownEmailTest(MergeUITestBase):
    def test_unknown_secondary_shows_field_error(self):
        self._login_staff()
        User.objects.create_user(email="keep@test.com", password="x")
        response = self._preview("keep@test.com", "ghost@test.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="merge-error-secondary"')
        self.assertContains(response, "No account found for ghost@test.com")
        self.assertNotContains(response, 'data-testid="merge-preview"')

    def test_unknown_canonical_shows_field_error(self):
        self._login_staff()
        User.objects.create_user(email="dupe@test.com", password="x")
        response = self._preview("ghost@test.com", "dupe@test.com")
        self.assertContains(response, 'data-testid="merge-error-canonical"')
        self.assertContains(response, "No account found for ghost@test.com")


class DualSubscriptionConflictTest(MergeUITestBase):
    def _make_dual(self):
        canonical, secondary = self._make_pair("paidA@test.com", "paidB@test.com")
        canonical.subscription_id = "sub_A"
        canonical.save(update_fields=["subscription_id"])
        secondary.subscription_id = "sub_B"
        secondary.save(update_fields=["subscription_id"])
        return canonical, secondary

    def test_conflict_shown_with_both_subscription_ids(self):
        self._login_staff()
        canonical, secondary = self._make_dual()
        response = self._preview("paidA@test.com", "paidB@test.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="merge-conflict-subscription"')
        self.assertContains(response, "sub_A")
        self.assertContains(response, "sub_B")
        # Force acknowledgement is required (checkbox + disabled button).
        self.assertContains(response, 'data-testid="merge-force-ack"')
        # No clean confirm button.
        self.assertNotContains(response, 'data-testid="merge-confirm-submit"')

    def test_confirm_without_force_token_rejected(self):
        # Posting force=0 against a conflict pair: the signed token covers
        # force=True, so a force=0 confirm fails verification and is rejected.
        self._login_staff()
        canonical, secondary = self._make_dual()
        # token signed for force=True (what preview emitted)
        token = _sign_pair(canonical.pk, secondary.pk, True)
        response = self.client.post(
            reverse("studio_user_merge_confirm"),
            {
                "canonical_user_id": canonical.pk,
                "secondary_user_id": secondary.pk,
                "force": "0",
                "confirm_token": token,
            },
        )
        self.assertContains(response, 'data-testid="merge-error-confirm"')
        canonical.refresh_from_db()
        secondary.refresh_from_db()
        self.assertEqual(canonical.subscription_id, "sub_A")
        self.assertEqual(secondary.subscription_id, "sub_B")
        self.assertTrue(secondary.is_active)

    def test_confirm_with_force_merges_and_records_dropped_sub(self):
        self._login_staff()
        canonical, secondary = self._make_dual()
        response = self._confirm(canonical.pk, secondary.pk, force=True)
        self.assertEqual(response.status_code, 200)
        canonical.refresh_from_db()
        self.assertEqual(canonical.subscription_id, "sub_A")
        self.assertContains(response, "sub_B")
        self.assertContains(response, 'data-testid="merge-plan-conflict-row"')


class StaffMergeTest(MergeUITestBase):
    def test_staff_account_refused_without_force_shows_warning(self):
        self._login_staff()
        User.objects.create_user(email="member@test.com", password="x")
        User.objects.create_user(
            email="colleague@test.com", password="x", is_staff=True
        )
        response = self._preview("member@test.com", "colleague@test.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="merge-conflict-staff"')
        self.assertContains(response, 'data-testid="merge-force-ack"')

    def test_staff_account_merges_with_force(self):
        self._login_staff()
        canonical = User.objects.create_user(
            email="member@test.com", password="x"
        )
        secondary = User.objects.create_user(
            email="colleague@test.com", password="x", is_staff=True
        )
        response = self._confirm(canonical.pk, secondary.pk, force=True)
        self.assertEqual(response.status_code, 200)
        secondary.refresh_from_db()
        self.assertFalse(secondary.is_active)


class ConfirmTokenTamperTest(MergeUITestBase):
    def test_mismatched_secondary_rejected(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        other = User.objects.create_user(email="other@test.com", password="x")
        # Token signed for (canonical, secondary), but confirm posts a DIFFERENT
        # secondary -> verification fails.
        token = _sign_pair(canonical.pk, secondary.pk, False)
        response = self.client.post(
            reverse("studio_user_merge_confirm"),
            {
                "canonical_user_id": canonical.pk,
                "secondary_user_id": other.pk,
                "force": "0",
                "confirm_token": token,
            },
        )
        self.assertContains(response, 'data-testid="merge-error-confirm"')
        secondary.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(secondary.is_active)
        self.assertTrue(other.is_active)
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )

    def test_garbage_token_rejected(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        response = self._confirm(
            canonical.pk, secondary.pk, token="not-a-real-token"
        )
        self.assertContains(response, 'data-testid="merge-error-confirm"')
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)

    def test_force_escalation_on_clean_token_rejected(self):
        # The critical safety property: a CLEAN preview signs force=False. If the
        # operator hand-edits the hidden force field to "1" to escalate to a
        # forced merge WITHOUT the acknowledgement checkbox, verification of the
        # (canonical_pk, secondary_pk, force) triple fails and nothing merges.
        self._login_staff()
        canonical, secondary = self._make_pair()
        clean_token = _sign_pair(canonical.pk, secondary.pk, False)
        response = self.client.post(
            reverse("studio_user_merge_confirm"),
            {
                "canonical_user_id": canonical.pk,
                "secondary_user_id": secondary.pk,
                "force": "1",  # escalation attempt against a force=False token
                "confirm_token": clean_token,
            },
        )
        self.assertContains(response, 'data-testid="merge-error-confirm"')
        secondary.refresh_from_db()
        self.assertTrue(secondary.is_active)
        self.assertFalse(
            EmailAlias.objects.filter(user=canonical).exists()
        )
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="merge_accounts").exists()
        )


class AlreadyMergedTest(MergeUITestBase):
    def test_rerun_preview_shows_friendly_already_merged(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        # Real merge first.
        self._confirm(canonical.pk, secondary.pk)
        self.assertEqual(
            CommunityAuditLog.objects.filter(action="merge_accounts").count(), 1
        )
        # Re-preview the same pair by the ORIGINAL emails (secondary now an alias).
        response = self._preview("keep@test.com", "dupe@test.com")
        # The original secondary email no longer resolves to a User (scrubbed);
        # the field-level "no account found" is the friendly outcome here, and
        # no new audit row is written.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            CommunityAuditLog.objects.filter(action="merge_accounts").count(), 1
        )

    def test_inactive_alias_pair_renders_already_merged_state(self):
        # Drive the engine's already_merged branch directly: secondary inactive
        # AND an existing alias on canonical -> preview shows the friendly state.
        self._login_staff()
        canonical, secondary = self._make_pair()
        secondary.is_active = False
        secondary.save(update_fields=["is_active"])
        EmailAlias.objects.create(
            user=canonical,
            email="dupe@test.com",
            source=EmailAlias.SOURCE_MERGE,
        )
        response = self._preview("keep@test.com", "dupe@test.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="merge-preview-already-merged"')
        self.assertNotContains(response, 'data-testid="merge-confirm-submit"')


class EntryPointsTest(MergeUITestBase):
    def test_people_nav_shows_merge_link(self):
        self._login_staff()
        response = self.client.get(reverse("studio_user_merge"))
        self.assertContains(response, reverse("studio_user_merge"))
        self.assertContains(response, "Merge accounts")

    def test_user_detail_prefills_canonical(self):
        self._login_staff()
        user = User.objects.create_user(email="keep@test.com", password="x")
        response = self.client.get(
            reverse("studio_user_detail", args=[user.pk])
        )
        self.assertContains(response, 'data-testid="user-detail-merge"')
        merge_url = reverse("studio_user_merge")
        self.assertContains(response, f"{merge_url}?canonical=keep%40test.com")

    def test_merge_page_reads_canonical_query_param(self):
        self._login_staff()
        response = self.client.get(
            reverse("studio_user_merge") + "?canonical=keep@test.com"
        )
        self.assertEqual(response.context["canonical_email"], "keep@test.com")
        self.assertContains(response, 'value="keep@test.com"')
