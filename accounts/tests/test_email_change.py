from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.core.cache import cache
from django.test import Client, TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import EmailAlias, EmailChangeRequest, MemberAPIKey, User
from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER
from accounts.services.email_change import (
    EMAIL_CHANGE_CONFIRM_TEMPLATE,
    EMAIL_CHANGED_NOTICE_TEMPLATE,
    confirm_email_change,
    request_email_change,
)
from accounts.services.email_resolution import resolve_user_by_email
from email_app.models import EmailLog
from payments.models import Tier


@tag("core")
@override_settings(SES_ENABLED=False)
class EmailChangeAccountPageTest(TestCase):
    def test_activated_member_tiers_and_staff_have_no_login_email_card(self):
        for index, tier_slug in enumerate(["free", "basic", "main", "premium"]):
            with self.subTest(tier=tier_slug):
                user = User.objects.create_user(
                    email=f"member-{index}@test.com",
                    password="CorrectPass123!",
                    account_activated=True,
                    email_verified=True,
                    tier=Tier.objects.get(slug=tier_slug),
                )
                self.client.force_login(user)

                response = self.client.get("/account/")

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="membership-section"')
                self.assertContains(response, 'id="email-preferences-section"')
                self.assertNotContains(response, 'id="login-email-section"')
                self.assertNotContains(response, 'data-testid="current-login-email"')
                self.assertNotContains(response, 'data-testid="change-email-form"')
                self.assertNotIn("pending_email_change", response.context)
                self.assertNotIn("email_change_requires_password", response.context)

        staff = User.objects.create_user(
            email="staff@test.com",
            password="CorrectPass123!",
            account_activated=True,
            email_verified=True,
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="login-email-section"')
        self.assertNotContains(response, 'data-testid="change-email-form"')

    def test_newsletter_only_account_does_not_show_email_change_form(self):
        user = User.objects.create_user(
            email="newsletter@test.com",
            password="CorrectPass123!",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'id="newsletter-only-cta"')
        self.assertNotContains(response, 'id="login-email-section"')
        self.assertNotContains(response, 'data-testid="change-email-form"')


@tag("core")
@override_settings(SES_ENABLED=False)
class EmailChangeRequestRouteWithdrawalTest(TestCase):
    url = "/account/api/change-email/request"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="old-member@test.com",
            password="CorrectPass123!",
            account_activated=True,
            email_verified=True,
        )
        self.client.force_login(self.user)

    def test_get_and_post_return_404_without_side_effects_for_all_callers(self):
        payload = {
            "new_email": "new-member@test.com",
            "current_password": "CorrectPass123!",
        }
        for authenticated in (True, False):
            if authenticated:
                self.client.force_login(self.user)
            else:
                self.client.logout()
            for method in ("get", "post"):
                with self.subTest(authenticated=authenticated, method=method):
                    response = getattr(self.client, method)(
                        self.url,
                        data=payload,
                        content_type=(
                            "application/json" if method == "post" else None
                        ),
                    )
                    self.assertEqual(response.status_code, 404)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")
        self.assertFalse(EmailChangeRequest.objects.exists())
        self.assertFalse(EmailLog.objects.exists())

    def test_anonymous_post_without_csrf_cookie_still_returns_404(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            self.url,
            data={"new_email": "new-member@test.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")
        self.assertFalse(EmailChangeRequest.objects.exists())
        self.assertFalse(EmailLog.objects.exists())


@tag("core")
@override_settings(SES_ENABLED=False)
class EmailChangeConfirmServiceTest(TestCase):
    def setUp(self):
        cache.clear()
        self.basic = Tier.objects.get(slug="basic")
        self.user = User.objects.create_user(
            email="old-member@test.com",
            password="CorrectPass123!",
            account_activated=True,
            email_verified=False,
            verification_expires_at=timezone.now() + timedelta(days=2),
        )
        self.user.tier = self.basic
        self.user.subscription_id = "sub_keep"
        self.user.stripe_customer_id = "cus_keep"
        self.user.billing_period_end = timezone.now() + timedelta(days=30)
        self.user.email_preferences = {"newsletter": True}
        self.user.slack_member = True
        self.user.slack_user_id = "U123"
        self.user.slack_checked_at = timezone.now()
        self.user.save()
        MemberAPIKey.create_for_user(user=self.user, name="local")
        SocialAccount.objects.create(
            user=self.user,
            provider="google",
            uid="google-uid-email-change",
        )

    def test_valid_confirmation_switches_email_and_preserves_account_state(self):
        EmailAddress.objects.create(
            user=self.user,
            email="old-member@test.com",
            primary=True,
            verified=True,
        )
        EmailAddress.objects.create(
            user=self.user,
            email="other-verified@test.com",
            primary=False,
            verified=True,
        )
        request_obj, token = request_email_change(
            self.user,
            "new-member@test.com",
            current_password="CorrectPass123!",
            send=False,
        )
        original_key_ids = set(
            MemberAPIKey.objects.filter(user=self.user).values_list("id", flat=True)
        )
        original_social_ids = set(
            SocialAccount.objects.filter(user=self.user).values_list("id", flat=True)
        )

        result = confirm_email_change(token)

        self.assertTrue(result.success)
        self.user.refresh_from_db()
        request_obj.refresh_from_db()
        self.assertEqual(self.user.email, "new-member@test.com")
        self.assertTrue(self.user.email_verified)
        self.assertIsNone(self.user.verification_expires_at)
        self.assertEqual(self.user.tier_id, self.basic.id)
        self.assertEqual(self.user.subscription_id, "sub_keep")
        self.assertEqual(self.user.stripe_customer_id, "cus_keep")
        self.assertEqual(self.user.email_preferences, {"newsletter": True})
        self.assertTrue(self.user.check_password("CorrectPass123!"))
        self.assertEqual(
            set(MemberAPIKey.objects.filter(user=self.user).values_list("id", flat=True)),
            original_key_ids,
        )
        self.assertEqual(
            set(SocialAccount.objects.filter(user=self.user).values_list("id", flat=True)),
            original_social_ids,
        )
        self.assertIsNotNone(request_obj.confirmed_at)

        alias = EmailAlias.objects.get(email="old-member@test.com")
        self.assertEqual(alias.user_id, self.user.pk)
        self.assertEqual(alias.source, EmailAlias.SOURCE_ACCOUNT_CHANGE)
        self.assertEqual(resolve_user_by_email("old-member@test.com"), self.user)

        new_address = EmailAddress.objects.get(
            user=self.user,
            email="new-member@test.com",
        )
        self.assertTrue(new_address.primary)
        self.assertTrue(new_address.verified)
        self.assertFalse(
            EmailAddress.objects.get(
                user=self.user,
                email="old-member@test.com",
            ).primary
        )
        self.assertTrue(
            EmailAddress.objects.get(
                user=self.user,
                email="other-verified@test.com",
            ).verified
        )

    def test_valid_confirmation_sends_notice_to_old_email_without_token(self):
        _request_obj, token = request_email_change(
            self.user,
            "new-member@test.com",
            current_password="CorrectPass123!",
            send=False,
        )

        with patch(
            "email_app.services.email_service.EmailService._send_ses",
            return_value="ses-notice",
        ) as send_mock:
            result = confirm_email_change(token)

        self.assertTrue(result.success)
        notice_call = next(
            call for call in send_mock.call_args_list
            if call.kwargs["email_type"] == EMAIL_CHANGED_NOTICE_TEMPLATE
        )
        self.assertEqual(notice_call.args[0], "old-member@test.com")
        notice_html = notice_call.args[2]
        self.assertIn("new-member@test.com", notice_html)
        self.assertNotIn("change-email/confirm", notice_html)
        self.assertNotIn("token=", notice_html)

    def test_confirmation_clears_slack_checked_at_but_preserves_slack_identity(self):
        _request_obj, token = request_email_change(
            self.user,
            "new-member@test.com",
            current_password="CorrectPass123!",
            send=False,
        )

        result = confirm_email_change(token)

        self.assertTrue(result.success)
        self.user.refresh_from_db()
        self.assertTrue(self.user.slack_member)
        self.assertEqual(self.user.slack_user_id, "U123")
        self.assertIsNone(self.user.slack_checked_at)

    def test_same_user_alias_is_promoted_to_primary(self):
        EmailAlias.objects.create(
            user=self.user,
            email="billing@test.com",
            source=EmailAlias.SOURCE_STRIPE_RELAY,
        )
        _request_obj, token = request_email_change(
            self.user,
            "billing@test.com",
            current_password="CorrectPass123!",
            send=False,
        )

        result = confirm_email_change(token)

        self.assertTrue(result.success)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "billing@test.com")
        self.assertFalse(EmailAlias.objects.filter(email="billing@test.com").exists())
        self.assertTrue(
            EmailAlias.objects.filter(
                user=self.user,
                email="old-member@test.com",
                source=EmailAlias.SOURCE_ACCOUNT_CHANGE,
            ).exists()
        )
        self.assertEqual(self.user.stripe_customer_id, "cus_keep")
        self.assertEqual(self.user.subscription_id, "sub_keep")

    def test_expired_reused_malformed_and_superseded_links_do_not_change_email(self):
        expired_request, expired_token = request_email_change(
            self.user,
            "expired@test.com",
            current_password="CorrectPass123!",
            send=False,
        )
        EmailChangeRequest.objects.filter(pk=expired_request.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        expired = confirm_email_change(expired_token)

        self.assertFalse(expired.success)
        self.assertEqual(expired.status, "expired")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")

        malformed = confirm_email_change("not-a-real-token")
        self.assertFalse(malformed.success)
        self.assertEqual(malformed.status, "malformed")

        cache.clear()
        first, first_token = request_email_change(
            self.user,
            "first@test.com",
            current_password="CorrectPass123!",
            send=False,
        )
        cache.clear()
        _second, second_token = request_email_change(
            self.user,
            "second@test.com",
            current_password="CorrectPass123!",
            send=False,
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.invalidated_at)

        superseded = confirm_email_change(first_token)
        self.assertFalse(superseded.success)
        self.assertEqual(superseded.status, "superseded")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")

        confirmed = confirm_email_change(second_token)
        self.assertTrue(confirmed.success)
        reused = confirm_email_change(second_token)
        self.assertFalse(reused.success)
        self.assertEqual(reused.status, "reused")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "second@test.com")

    def test_confirmation_rechecks_collisions_before_saving(self):
        _request_obj, token = request_email_change(
            self.user,
            "new-member@test.com",
            current_password="CorrectPass123!",
            send=False,
        )
        User.objects.create_user(email="new-member@test.com")

        result = confirm_email_change(token)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "collision")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")
        self.assertFalse(EmailAlias.objects.filter(email="old-member@test.com").exists())

    def test_request_email_has_no_extra_verify_footer(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])

        with patch(
            "email_app.services.email_service.EmailService._send_ses",
            return_value="ses-confirm",
        ) as send_mock:
            request_email_change(
                self.user,
                "new-member@test.com",
                current_password="CorrectPass123!",
            )

        confirm_call = next(
            call for call in send_mock.call_args_list
            if call.kwargs["email_type"] == EMAIL_CHANGE_CONFIRM_TEMPLATE
        )
        self.assertEqual(confirm_call.args[0], "new-member@test.com")
        confirm_html = confirm_call.args[2]
        self.assertIn("change-email/confirm", confirm_html)
        self.assertNotIn("/api/verify-email", confirm_html)
