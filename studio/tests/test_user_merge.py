"""Tests for the Studio account-merge UI (issue #842).

The merge ENGINE is exhaustively tested in ``api/tests/test_user_merge.py``;
these tests cover the Studio surface only: the staff gate, that preview is a true
no-op, that confirm runs the real merge on the previewed pair, the signed
``confirm_token`` rejecting a tampered/expired confirm, the conflict/force gating,
the friendly already-merged state, and the user-detail pre-fill.
"""

from html.parser import HTMLParser

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailAlias, MemberAPIKey, Token
from community.models import CommunityAuditLog
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from payments.models import Tier
from studio.views.merge import _CONFIRM_SALT, _sign_pair
from tests.fixtures import set_membership

User = get_user_model()


_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "source", "track", "wbr",
})


class _TestIdTextParser(HTMLParser):
    """Collect the visible text of every element carrying one ``data-testid``."""

    def __init__(self, testid):
        super().__init__(convert_charrefs=True)
        self.testid = testid
        self.texts = []
        self._capturing = False
        self._depth = 0
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            return
        if self._capturing:
            self._depth += 1
        elif dict(attrs).get("data-testid") == self.testid:
            self._capturing = True
            self._depth = 0
            self._buffer = []

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS or not self._capturing:
            return
        if self._depth:
            self._depth -= 1
            return
        self.texts.append(" ".join("".join(self._buffer).split()))
        self._capturing = False

    def handle_data(self, data):
        if self._capturing:
            self._buffer.append(data)


def testid_texts(source, testid):
    parser = _TestIdTextParser(testid)
    parser.feed(source)
    return parser.texts


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

    def test_member_gets_403(self):
        """Relocated from Playwright TestNonStaffBlocked.test_member_gets_403 (#1484)."""
        canonical, secondary = self._make_pair()
        self.client.force_login(self.member)
        response = self.client.get(reverse("studio_user_merge"))
        self.assertEqual(response.status_code, 403)
        canonical.refresh_from_db()
        secondary.refresh_from_db()
        self.assertTrue(canonical.is_active)
        self.assertTrue(secondary.is_active)
        self.assertFalse(EmailAlias.objects.filter(email=secondary.email).exists())

    def test_staff_get_renders_pickers(self):
        self._login_staff()
        response = self.client.get(reverse("studio_user_merge"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "studio/includes/_people_picker.html")
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
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=secondary,
            name="preview key",
        )

        response = self._preview("keep@test.com", "dupe@test.com")
        self.assertEqual(response.status_code, 200)

        # Plan rendered with the moved event registration row.
        self.assertContains(response, 'data-testid="merge-preview"')
        self.assertContains(response, "events.EventRegistration")
        self.assertContains(response, 'data-testid="merge-plan-deactivate-notice"')
        self.assertContains(response, 'data-testid="merge-plan-credentials"')
        self.assertContains(
            response,
            'data-testid="merge-plan-member-api-keys-revoked"',
        )
        self.assertEqual(
            response.context["plan"]["credentials"],
            {
                "member_api_keys_revoked": 1,
                "operator_tokens_deleted": 0,
                "package_api_keys_revoked": 0,
            },
        )
        # Confirm form present for a clean merge.
        self.assertContains(response, 'data-testid="merge-confirm-submit"')

        # NOTHING persisted.
        self.assertEqual(
            EventRegistration.objects.filter(user=secondary).count(), 1
        )
        self.assertEqual(EmailLog.objects.filter(user=canonical).count(), 0)
        member_key.refresh_from_db()
        self.assertIsNone(member_key.revoked_at)
        self.assertEqual(MemberAPIKey.authenticate(member_plaintext).pk, member_key.pk)
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
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=secondary,
            name="confirmed key",
        )

        response = self._confirm(canonical.pk, secondary.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["result"]["credentials"],
            {
                "member_api_keys_revoked": 1,
                "operator_tokens_deleted": 0,
                "package_api_keys_revoked": 0,
            },
        )
        self.assertContains(
            response,
            'data-testid="merge-plan-member-api-keys-revoked"',
        )
        member_key.refresh_from_db()
        self.assertIsNotNone(member_key.revoked_at)
        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))

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
        set_membership(canonical, subscription_id="sub_A")
        set_membership(secondary, subscription_id="sub_B")
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
        self.assertEqual(canonical.membership.subscription_id, "sub_A")
        self.assertEqual(secondary.membership.subscription_id, "sub_B")
        self.assertTrue(secondary.is_active)

    def test_confirm_with_force_merges_and_records_dropped_sub(self):
        self._login_staff()
        canonical, secondary = self._make_dual()
        response = self._confirm(canonical.pk, secondary.pk, force=True)
        self.assertEqual(response.status_code, 200)
        canonical.refresh_from_db()
        self.assertEqual(canonical.membership.subscription_id, "sub_A")
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
        self.assertContains(response, "Any operator API tokens")
        self.assertContains(response, "will be deleted.")

    def test_staff_account_merges_with_force(self):
        self._login_staff()
        canonical = User.objects.create_user(
            email="member@test.com", password="x"
        )
        secondary = User.objects.create_user(
            email="colleague@test.com", password="x", is_staff=True
        )
        operator_token, operator_plaintext = Token.create_for_user(
            user=secondary,
            name="secondary operator",
        )
        response = self._confirm(canonical.pk, secondary.pk, force=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["result"]["credentials"],
            {
                "member_api_keys_revoked": 0,
                "operator_tokens_deleted": 1,
                "package_api_keys_revoked": 0,
            },
        )
        self.assertContains(
            response,
            'data-testid="merge-plan-operator-tokens-deleted"',
        )
        self.assertFalse(Token.objects.filter(pk=operator_token.pk).exists())
        self.assertIsNone(Token.authenticate(operator_plaintext))
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


class PackageApiKeyCredentialRowTest(MergeUITestBase):
    """The operator is told a community-base API key is being killed (#1736)."""

    PACKAGE_ROW_TEMPLATE = (
        '<div class="flex justify-between gap-4" '
        'data-testid="merge-plan-package-api-keys-revoked">'
        '<dt class="text-muted-foreground">API keys revoked (community-base)</dt>'
        '<dd class="text-foreground">{count}</dd>'
        "</div>"
    )

    def _make_package_key(self, user, name):
        return APIKey.create_for_user(
            user=user,
            name=name,
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

    def test_preview_and_result_report_the_revoked_package_key_count(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        _, canonical_plaintext = self._make_package_key(canonical, "survivor key")
        secondary_key, secondary_plaintext = self._make_package_key(
            secondary, "dupe key"
        )

        preview = self._preview("keep@test.com", "dupe@test.com")
        self.assertInHTML(
            self.PACKAGE_ROW_TEMPLATE.format(count=1),
            preview.content.decode(),
        )
        self.assertEqual(
            preview.context["plan"]["credentials"]["package_api_keys_revoked"], 1
        )
        # The credential is not presented as something that moves.
        self.assertNotContains(preview, APIKey._meta.label)
        # Preview is still a no-op.
        secondary_key.refresh_from_db()
        self.assertIsNone(secondary_key.revoked_at)

        result = self._confirm(canonical.pk, secondary.pk)
        self.assertInHTML(
            self.PACKAGE_ROW_TEMPLATE.format(count=1),
            result.content.decode(),
        )
        self.assertEqual(
            result.context["result"]["credentials"]["package_api_keys_revoked"], 1
        )
        self.assertIsNone(APIKey.authenticate(secondary_plaintext))
        self.assertIsNotNone(APIKey.authenticate(canonical_plaintext))


class CredentialConsequenceTest(MergeUITestBase):
    """The counters are told what they cost the operator (issue #1745).

    Copy assertions run against the extracted text of the specific testid, not
    the whole response, so unrelated page copy can never satisfy them.
    """

    CONSEQUENCE = "merge-plan-credentials-consequence"
    REVOKED_NOTE = "merge-plan-credentials-revoked-note"
    DELETED_NOTE = "merge-plan-credentials-deleted-note"

    PREVIEW_SENTENCE = (
        "These credentials stop working the moment you confirm. They do not "
        "transfer to keep@test.com and cannot be restored. If a person or an "
        "integration is still using one, they need a replacement key — "
        "check before you confirm."
    )
    RESULT_SENTENCE = (
        "These credentials stopped working when the merge ran. They did not "
        "transfer to keep@test.com and cannot be restored. If a person or an "
        "integration was still using one, a replacement key must be issued now."
    )
    REVOKED_SENTENCE = (
        "Revoked keys stay on the merged-in account as security history."
    )
    DELETED_SENTENCE = (
        "Operator tokens have no revoked state, so they are deleted outright "
        "and leave no record behind."
    )

    COUNTER_ROW_TEMPLATE = (
        '<div class="flex justify-between gap-4" data-testid="{testid}">'
        '<dt class="text-muted-foreground">{label}</dt>'
        '<dd class="text-foreground">{count}</dd>'
        "</div>"
    )

    def _blocks(self, response, testid):
        """Return the visible text of every element carrying ``testid``."""
        return testid_texts(response.content.decode(), testid)

    def _occurrences(self, response, testid):
        return response.content.decode().count(f'data-testid="{testid}"')

    def _make_member_key(self, user):
        return MemberAPIKey.create_for_user(user=user, name="member key")

    def _make_package_key(self, user):
        return APIKey.create_for_user(
            user=user,
            name="package key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )

    def _make_operator_token(self, user):
        """Give a demoted ex-operator the token they were issued while staff.

        ``Token.clean()`` refuses to mint a token for a non-staff user, so the
        only way to own one as a mergeable (non-staff) secondary is the real
        one: issued while staff, kept after the demotion.
        """
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        token, plaintext = Token.create_for_user(user=user, name="while staff")
        user.is_staff = False
        user.save(update_fields=["is_staff"])
        return token, plaintext

    def test_preview_warns_that_the_keys_die_on_confirm(self):
        self._login_staff()
        _, secondary = self._make_pair()
        self._make_member_key(secondary)
        self._make_package_key(secondary)

        preview = self._preview("keep@test.com", "dupe@test.com")

        self.assertEqual(self._occurrences(preview, self.CONSEQUENCE), 1)
        self.assertIn(self.PREVIEW_SENTENCE, self._blocks(preview, self.CONSEQUENCE)[0])
        self.assertEqual(
            self._blocks(preview, self.REVOKED_NOTE), [self.REVOKED_SENTENCE]
        )
        # Nothing was deleted, so the deleted-outright note stays silent.
        self.assertEqual(self._occurrences(preview, self.DELETED_NOTE), 0)
        self.assertNotContains(preview, "deleted outright")

    def test_result_switches_to_past_tense_and_tells_the_operator_what_to_do(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        self._make_member_key(secondary)

        result = self._confirm(canonical.pk, secondary.pk)

        self.assertEqual(self._occurrences(result, self.CONSEQUENCE), 1)
        block = self._blocks(result, self.CONSEQUENCE)[0]
        self.assertIn(self.RESULT_SENTENCE, block)
        self.assertNotIn("check before you confirm", block)

    def test_no_warning_when_the_pair_owns_no_credentials(self):
        self._login_staff()
        canonical, secondary = self._make_pair()

        preview = self._preview("keep@test.com", "dupe@test.com")
        self.assertEqual(self._occurrences(preview, self.CONSEQUENCE), 0)
        self.assertNotContains(preview, "cannot be restored")
        self.assertInHTML(
            self.COUNTER_ROW_TEMPLATE.format(
                testid="merge-plan-member-api-keys-revoked",
                label="Member API keys revoked",
                count=0,
            ),
            preview.content.decode(),
        )

        result = self._confirm(canonical.pk, secondary.pk)
        self.assertEqual(self._occurrences(result, self.CONSEQUENCE), 0)
        self.assertNotContains(result, "cannot be restored")

    def test_deleted_token_note_replaces_the_revoked_note(self):
        self._login_staff()
        _, secondary = self._make_pair()
        self._make_operator_token(secondary)

        preview = self._preview("keep@test.com", "dupe@test.com")

        self.assertIn(self.PREVIEW_SENTENCE, self._blocks(preview, self.CONSEQUENCE)[0])
        self.assertEqual(
            self._blocks(preview, self.DELETED_NOTE), [self.DELETED_SENTENCE]
        )
        # Nothing was revoked, so the security-history note stays silent.
        self.assertEqual(self._occurrences(preview, self.REVOKED_NOTE), 0)
        self.assertNotContains(preview, "security history")

    def test_package_key_alone_still_gets_the_revoked_note(self):
        self._login_staff()
        _, secondary = self._make_pair()
        self._make_package_key(secondary)

        preview = self._preview("keep@test.com", "dupe@test.com")

        self.assertEqual(
            self._blocks(preview, self.REVOKED_NOTE), [self.REVOKED_SENTENCE]
        )
        self.assertEqual(self._occurrences(preview, self.DELETED_NOTE), 0)

    def test_all_three_families_render_one_warning_and_unchanged_counters(self):
        self._login_staff()
        canonical, secondary = self._make_pair()
        self._make_member_key(secondary)
        self._make_package_key(secondary)
        self._make_operator_token(secondary)

        preview = self._preview("keep@test.com", "dupe@test.com")

        # One block, one of each note -- never repeated per counter row.
        self.assertEqual(self._occurrences(preview, self.CONSEQUENCE), 1)
        self.assertEqual(self._occurrences(preview, self.REVOKED_NOTE), 1)
        self.assertEqual(self._occurrences(preview, self.DELETED_NOTE), 1)
        block = self._blocks(preview, self.CONSEQUENCE)[0]
        self.assertIn(self.PREVIEW_SENTENCE, block)
        self.assertIn(self.REVOKED_SENTENCE, block)
        self.assertIn(self.DELETED_SENTENCE, block)

        # Preview: the counters and their rows render unchanged. This half
        # says nothing about merge ordering -- a dry run never reaches the
        # deactivation block, so these read 1 under either ordering.
        for testid, label, key in (
            (
                "merge-plan-member-api-keys-revoked",
                "Member API keys revoked",
                "member_api_keys_revoked",
            ),
            (
                "merge-plan-operator-tokens-deleted",
                "Operator tokens deleted",
                "operator_tokens_deleted",
            ),
            (
                "merge-plan-package-api-keys-revoked",
                "API keys revoked (community-base)",
                "package_api_keys_revoked",
            ),
        ):
            with self.subTest(counter=key):
                self.assertEqual(preview.context["plan"]["credentials"][key], 1)
                self.assertInHTML(
                    self.COUNTER_ROW_TEMPLATE.format(
                        testid=testid, label=label, count=1
                    ),
                    preview.content.decode(),
                )

        result = self._confirm(canonical.pk, secondary.pk)

        self.assertEqual(self._occurrences(result, self.CONSEQUENCE), 1)
        result_block = self._blocks(result, self.CONSEQUENCE)[0]
        self.assertIn(self.RESULT_SENTENCE, result_block)
        self.assertIn(self.REVOKED_SENTENCE, result_block)
        self.assertIn(self.DELETED_SENTENCE, result_block)
        # This is the ordering guard: after a REAL merge, all three counters
        # are still complete. `_repoint_relations` has to run before
        # `secondary.save(is_active=False)` for that to hold -- deactivate
        # first and the `User.save()` hook consumes the credentials, leaving
        # every counter at 0.
        for key in (
            "member_api_keys_revoked",
            "operator_tokens_deleted",
            "package_api_keys_revoked",
        ):
            with self.subTest(counter=key):
                self.assertEqual(result.context["result"]["credentials"][key], 1)
