"""Login / auth resolution through EmailAlias (issue #845).

Every "after a merge" fixture is built with the REAL merge engine
(``accounts.services.account_merge.merge_accounts``) so the tests exercise the
true post-merge state (deactivated + scrubbed secondary, alias recorded), not a
hand-built approximation.
"""

import json
from unittest.mock import patch

from allauth.core.exceptions import SignupClosedException
from allauth.socialaccount.internal.flows.signup import process_signup
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase, override_settings, tag

from accounts.adapters import SocialAccountAdapter
from accounts.models import EmailAlias, User
from accounts.services.account_merge import (
    SCRUBBED_EMAIL_SUFFIX,
    backfill_scrub_legacy_merged_emails,
    merge_accounts,
)
from accounts.services.email_resolution import resolve_user_by_email
from accounts.views.auth import INVALID_LOGIN_ERROR

FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CANONICAL_EMAIL = "alena@gmail.com"
ALIAS_EMAIL = "alena.work@gmail.com"
CANONICAL_PASSWORD = "Pcanonical123"
SECONDARY_PASSWORD = "Ssecondary456"


def _register_social_apps():
    """Create the SocialApp rows allauth's connect() looks up, on the test site."""
    site = Site.objects.get_current()
    for provider in ("google", "github", "slack"):
        app, _created = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={
                "name": provider,
                "client_id": f"{provider}-client",
                "secret": "secret",
            },
        )
        app.sites.add(site)


def _merge_pair(
    canonical_email=CANONICAL_EMAIL,
    alias_email=ALIAS_EMAIL,
    canonical_password=CANONICAL_PASSWORD,
    secondary_password=SECONDARY_PASSWORD,
):
    """Create canonical + secondary, merge the secondary in, return both rows."""
    canonical = User.objects.create_user(
        email=canonical_email, password=canonical_password
    )
    secondary = User.objects.create_user(
        email=alias_email, password=secondary_password
    )
    merge_accounts(
        canonical, secondary, actor_label="test", actor=None,
    )
    canonical.refresh_from_db()
    secondary.refresh_from_db()
    return canonical, secondary


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class AliasPasswordLoginTest(TestCase):
    url = "/api/login"

    def _post(self, data):
        return self.client.post(
            self.url, data=json.dumps(data), content_type="application/json"
        )

    def test_alias_email_with_canonical_password_signs_in_as_canonical(self):
        canonical, secondary = _merge_pair()
        resp = self._post({"email": ALIAS_EMAIL, "password": CANONICAL_PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        # Session user is the canonical account, never the secondary.
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), canonical.pk
        )
        self.assertNotEqual(
            int(self.client.session["_auth_user_id"]), secondary.pk
        )

    def test_alias_email_honours_safe_redirect(self):
        _merge_pair()
        resp = self._post({
            "email": ALIAS_EMAIL,
            "password": CANONICAL_PASSWORD,
            "next": "/events/demo?register=1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirect_url"], "/events/demo?register=1")

    def test_alias_email_with_wrong_password_is_rejected(self):
        _merge_pair()
        resp = self._post({"email": ALIAS_EMAIL, "password": "totally-wrong"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], INVALID_LOGIN_ERROR)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deactivated_secondary_cannot_log_in_with_its_old_password(self):
        # The secondary's own pre-merge password (S) must NOT work: we only ever
        # authenticate canonical (whose password is P), never the dead row.
        _merge_pair()
        resp = self._post({"email": ALIAS_EMAIL, "password": SECONDARY_PASSWORD})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], INVALID_LOGIN_ERROR)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_error_bodies_identical_across_failure_modes(self):
        # unknown email, deactivated-secondary-with-wrong-password, canonical
        # with an UNUSABLE password (OAuth-only), and a plain wrong password must
        # all return a byte-identical body so the alias relationship can't be
        # enumerated.
        _merge_pair()
        # OAuth-only canonical: usable-password=False, with an alias of its own.
        oauth_canonical = User.objects.create_user(
            email="oauth@gmail.com", password=None
        )
        oauth_canonical.set_unusable_password()
        oauth_canonical.save(update_fields=["password"])
        EmailAlias.objects.create(
            user=oauth_canonical,
            email="oauth.alias@gmail.com",
            source=EmailAlias.SOURCE_MANUAL,
        )

        bodies = []
        for email, password in [
            ("nobody@example.com", "whatever"),
            (ALIAS_EMAIL, "wrong-password"),
            ("oauth.alias@gmail.com", "anything"),
            (CANONICAL_EMAIL, "wrong-password"),
        ]:
            self.client.logout()
            resp = self._post({"email": email, "password": password})
            self.assertEqual(resp.status_code, 401)
            bodies.append(resp.content)

        self.assertEqual(len(set(bodies)), 1, bodies)
        self.assertEqual(
            json.loads(bodies[0])["error"], INVALID_LOGIN_ERROR
        )

    def test_no_duplicate_user_created_on_alias_login(self):
        _merge_pair()
        before = User.objects.count()
        self._post({"email": ALIAS_EMAIL, "password": CANONICAL_PASSWORD})
        self.assertEqual(User.objects.count(), before)


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class AliasPasswordResetTest(TestCase):
    url = "/api/password-reset-request"

    def _post(self, data):
        return self.client.post(
            self.url, data=json.dumps(data), content_type="application/json"
        )

    @patch("email_app.services.email_service.EmailService")
    def test_alias_reset_targets_canonical_primary_email(self, mock_service_cls):
        canonical, _secondary = _merge_pair()
        service = mock_service_cls.return_value

        resp = self._post({"email": ALIAS_EMAIL})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("If an account exists", resp.json()["message"])
        # Exactly one reset email, delivered to the CANONICAL primary email,
        # never to the typed alias.
        self.assertEqual(service.send.call_count, 1)
        sent_user = service.send.call_args.args[0]
        self.assertEqual(sent_user.pk, canonical.pk)
        self.assertEqual(sent_user.email, CANONICAL_EMAIL)
        self.assertNotEqual(sent_user.email, ALIAS_EMAIL)

    @patch("email_app.services.email_service.EmailService")
    def test_unknown_email_is_silently_non_revealing(self, mock_service_cls):
        service = mock_service_cls.return_value
        resp = self._post({"email": "nobody@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("If an account exists", resp.json()["message"])
        self.assertEqual(service.send.call_count, 0)


@tag("core")
class AliasOAuthAdapterTest(TestCase):
    """Drive the allauth pre_social_login adapter directly."""

    def setUp(self):
        self.factory = RequestFactory()
        self.adapter = SocialAccountAdapter()
        _register_social_apps()

    def _request(self):
        request = self.factory.get("/accounts/google/login/callback/")
        # connect() needs a session for some allauth bookkeeping paths.
        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        return request

    def test_first_ever_oauth_with_alias_connects_to_canonical(self):
        canonical, secondary = _merge_pair()
        before = User.objects.count()

        # First-ever login: an unsaved user carrying the verified alias email and
        # no prior linked SocialAccount.
        unsaved = User(email=ALIAS_EMAIL)
        account = SocialAccount(provider="google", uid="google-uid-1")
        sociallogin = SocialLogin(user=unsaved, account=account, email_addresses=[])

        self.adapter.pre_social_login(self._request(), sociallogin)

        # Connected to canonical, which is active.
        self.assertEqual(sociallogin.user.pk, canonical.pk)
        self.assertTrue(sociallogin.user.is_active)
        # No new User row.
        self.assertEqual(User.objects.count(), before)
        # The SocialAccount points at canonical, never the inactive secondary.
        sa = SocialAccount.objects.get(provider="google", uid="google-uid-1")
        self.assertEqual(sa.user_id, canonical.pk)
        self.assertFalse(
            SocialAccount.objects.filter(user_id=secondary.pk).exists()
        )

    def test_pre_linked_identity_still_lands_on_canonical(self):
        # Regression: an identity linked BEFORE the merge is repointed to
        # canonical by the merge engine. allauth resolves it via the existing
        # SocialAccount; the adapter must leave it alone.
        canonical = User.objects.create_user(
            email=CANONICAL_EMAIL, password=CANONICAL_PASSWORD
        )
        secondary = User.objects.create_user(
            email=ALIAS_EMAIL, password=SECONDARY_PASSWORD
        )
        # Identity linked to the secondary before the merge.
        SocialAccount.objects.create(
            user=secondary, provider="google", uid="google-uid-2"
        )
        merge_accounts(canonical, secondary, actor_label="test", actor=None)
        canonical.refresh_from_db()

        # The merge repointed the SocialAccount to canonical.
        sa = SocialAccount.objects.get(provider="google", uid="google-uid-2")
        self.assertEqual(sa.user_id, canonical.pk)

        # Re-signing in: allauth builds a sociallogin from the existing account.
        sociallogin = SocialLogin(
            user=canonical, account=sa, email_addresses=[]
        )
        self.assertTrue(sociallogin.is_existing)
        before = User.objects.count()
        self.adapter.pre_social_login(self._request(), sociallogin)

        self.assertEqual(sociallogin.user.pk, canonical.pk)
        self.assertEqual(User.objects.count(), before)

    def test_adapter_never_connects_to_inactive_secondary(self):
        _canonical, secondary = _merge_pair()
        unsaved = User(email=ALIAS_EMAIL)
        account = SocialAccount(provider="github", uid="gh-uid-1")
        sociallogin = SocialLogin(user=unsaved, account=account, email_addresses=[])

        self.adapter.pre_social_login(self._request(), sociallogin)

        self.assertNotEqual(sociallogin.user.pk, secondary.pk)
        self.assertFalse(
            SocialAccount.objects.filter(user_id=secondary.pk).exists()
        )

    def test_new_slack_identity_cannot_create_platform_user(self):
        before = User.objects.count()
        sociallogin = SocialLogin(
            user=User(email="brand-new-slack@example.com"),
            account=SocialAccount(provider="slack", uid="U-BRAND-NEW"),
            email_addresses=[],
        )

        request = self._request()
        self.assertFalse(self.adapter.is_open_for_signup(request, sociallogin))
        with self.assertRaises(SignupClosedException):
            process_signup(request, sociallogin)
        self.assertEqual(User.objects.count(), before)

    def test_existing_linked_slack_identity_remains_allowed(self):
        canonical = User.objects.create_user(email="linked-slack@example.com")
        account = SocialAccount.objects.create(
            user=canonical,
            provider="slack",
            uid="U-LINKED",
        )
        sociallogin = SocialLogin(
            user=canonical,
            account=account,
            email_addresses=[],
        )

        self.assertTrue(sociallogin.is_existing)
        self.assertTrue(
            self.adapter.is_open_for_signup(self._request(), sociallogin)
        )

    def test_slack_alias_resolution_remains_allowed(self):
        canonical, _secondary = _merge_pair()
        sociallogin = SocialLogin(
            user=User(email=ALIAS_EMAIL),
            account=SocialAccount(provider="slack", uid="U-ALIAS"),
            email_addresses=[],
        )

        self.adapter.pre_social_login(self._request(), sociallogin)

        self.assertEqual(sociallogin.user.pk, canonical.pk)
        self.assertTrue(sociallogin.is_existing)
        self.assertTrue(
            self.adapter.is_open_for_signup(self._request(), sociallogin)
        )


@tag("core")
class AliasInvariantAfterMergeTest(TestCase):
    def test_merge_scrubs_secondary_email_restoring_invariant(self):
        canonical, secondary = _merge_pair()

        # Secondary email is scrubbed: no User.email equals any EmailAlias.email.
        self.assertTrue(secondary.email.endswith(SCRUBBED_EMAIL_SUFFIX))
        alias_emails = set(EmailAlias.objects.values_list("email", flat=True))
        user_emails = set(
            e.lower() for e in User.objects.values_list("email", flat=True)
        )
        self.assertEqual(alias_emails & user_emails, set())

        # The original alias address still routes to canonical.
        alias = EmailAlias.objects.get(user=canonical, email=ALIAS_EMAIL)
        self.assertEqual(alias.user_id, canonical.pk)
        self.assertEqual(
            resolve_user_by_email(ALIAS_EMAIL).pk, canonical.pk
        )

    def test_data_migration_backfills_legacy_unscrubbed_secondary(self):
        # Reproduce the pre-#845 legacy state: a deactivated secondary whose
        # User.email STILL equals an EmailAlias.email, then run the migration's
        # backfill function and assert the invariant is restored.
        canonical = User.objects.create_user(
            email="legacy.canon@gmail.com", password="x"
        )
        secondary = User.objects.create_user(
            email="legacy.alias@gmail.com", password="x"
        )
        # Hand-build the broken legacy state: alias recorded but secondary email
        # left intact + deactivated (what the engine did before #845).
        EmailAlias.objects.create(
            user=canonical,
            email="legacy.alias@gmail.com",
            source=EmailAlias.SOURCE_MERGE,
        )
        secondary.is_active = False
        secondary.save(update_fields=["is_active"])

        scrubbed = backfill_scrub_legacy_merged_emails(User, EmailAlias)
        self.assertEqual(scrubbed, 1)

        secondary.refresh_from_db()
        self.assertTrue(secondary.email.endswith(SCRUBBED_EMAIL_SUFFIX))
        # Invariant restored, alias still routes to canonical.
        self.assertEqual(
            resolve_user_by_email("legacy.alias@gmail.com").pk, canonical.pk
        )

    def test_backfill_never_scrubs_an_active_user(self):
        # Highest-risk guard: the backfill must NEVER touch an ACTIVE account,
        # even in the pathological case where an active user's email happens to
        # equal an EmailAlias.email. Only deactivated (merged-away) secondaries
        # are eligible. Without the ``is_active=False`` filter this would scrub
        # a live login -- a data-loss bug -- so assert it explicitly.
        active = User.objects.create_user(
            email="active.collision@gmail.com", password="x"
        )
        EmailAlias.objects.create(
            user=active,
            email="active.collision@gmail.com",
            source=EmailAlias.SOURCE_MANUAL,
        )
        self.assertTrue(active.is_active)

        scrubbed = backfill_scrub_legacy_merged_emails(User, EmailAlias)

        self.assertEqual(scrubbed, 0)
        active.refresh_from_db()
        self.assertEqual(active.email, "active.collision@gmail.com")

    def test_backfill_leaves_non_merged_inactive_user_untouched(self):
        # A deactivated user whose email is NOT recorded as any EmailAlias was
        # never merged (e.g. a banned / disabled account). The backfill must not
        # scrub it -- only already-aliased secondaries are in scope.
        orphan = User.objects.create_user(
            email="disabled.user@gmail.com", password="x"
        )
        orphan.is_active = False
        orphan.save(update_fields=["is_active"])

        scrubbed = backfill_scrub_legacy_merged_emails(User, EmailAlias)

        self.assertEqual(scrubbed, 0)
        orphan.refresh_from_db()
        self.assertEqual(orphan.email, "disabled.user@gmail.com")

    def test_backfill_is_idempotent(self):
        # Re-running the backfill after it has already scrubbed must be a no-op:
        # an already-scrubbed secondary is skipped (matches the migration's
        # safe-to-re-run contract on prod).
        canonical = User.objects.create_user(
            email="idem.canon@gmail.com", password="x"
        )
        secondary = User.objects.create_user(
            email="idem.alias@gmail.com", password="x"
        )
        EmailAlias.objects.create(
            user=canonical,
            email="idem.alias@gmail.com",
            source=EmailAlias.SOURCE_MERGE,
        )
        secondary.is_active = False
        secondary.save(update_fields=["is_active"])

        first = backfill_scrub_legacy_merged_emails(User, EmailAlias)
        second = backfill_scrub_legacy_merged_emails(User, EmailAlias)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        # The alias row still holds the ORIGINAL email after both runs.
        self.assertTrue(
            EmailAlias.objects.filter(email="idem.alias@gmail.com").exists()
        )


@tag("core")
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class EndToEndPostMergeLoginTest(TestCase):
    """The Alena Fojtík case: merge via the engine, then log in both ways."""

    def test_password_and_oauth_both_land_on_canonical(self):
        _register_social_apps()
        canonical, secondary = _merge_pair()

        # 1. Password login with the merged-away email + canonical's password.
        resp = self.client.post(
            "/api/login",
            data=json.dumps(
                {"email": ALIAS_EMAIL, "password": CANONICAL_PASSWORD}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), canonical.pk
        )

        # 2. First-ever OAuth with the merged-away email resolves to canonical.
        factory = RequestFactory()
        request = factory.get("/accounts/google/login/callback/")
        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        unsaved = User(email=ALIAS_EMAIL)
        account = SocialAccount(provider="google", uid="e2e-uid")
        sociallogin = SocialLogin(user=unsaved, account=account, email_addresses=[])
        SocialAccountAdapter().pre_social_login(request, sociallogin)
        self.assertEqual(sociallogin.user.pk, canonical.pk)

        # The merged-away row stays inactive and is never the session user.
        secondary.refresh_from_db()
        self.assertFalse(secondary.is_active)
        self.assertEqual(
            User.objects.filter(email=CANONICAL_EMAIL).count(), 1
        )
