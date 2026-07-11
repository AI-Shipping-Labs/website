import json
from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import EmailAlias, EmailChangeRequest, MemberAPIKey, User
from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER
from accounts.services.email_change import (
    EMAIL_CHANGE_CONFIRM_TEMPLATE,
    EMAIL_CHANGED_NOTICE_TEMPLATE,
    active_email_change_request_for_user,
    confirm_email_change,
    request_email_change,
)
from accounts.services.email_resolution import resolve_user_by_email
from email_app.models import EmailLog
from payments.models import Tier


def _post_json(client, url, payload):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
    )


@tag("core")
@override_settings(SES_ENABLED=False)
class EmailChangeAccountPageTest(TestCase):
    def test_login_email_card_visible_for_activated_member(self):
        user = User.objects.create_user(
            email="member@test.com",
            password="CorrectPass123!",
            account_activated=True,
            email_verified=True,
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'id="login-email-section"')
        self.assertContains(response, 'data-testid="current-login-email"')
        self.assertContains(response, "member@test.com")
        self.assertContains(response, 'data-testid="change-email-form"')

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
class EmailChangeRequestEndpointTest(TestCase):
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

    def test_anonymous_user_redirected_by_existing_login_protection(self):
        self.client.logout()

        response = _post_json(
            self.client,
            self.url,
            {"new_email": "new-member@test.com"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertFalse(EmailChangeRequest.objects.exists())

    def test_password_required_for_password_bearing_accounts(self):
        response = _post_json(
            self.client,
            self.url,
            {"new_email": "new-member@test.com"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")
        self.assertFalse(EmailChangeRequest.objects.exists())
        self.assertFalse(
            EmailLog.objects.filter(
                email_type=EMAIL_CHANGE_CONFIRM_TEMPLATE,
            ).exists()
        )

    def test_wrong_password_does_not_send_or_change_email(self):
        response = _post_json(
            self.client,
            self.url,
            {
                "new_email": "new-member@test.com",
                "current_password": "WrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")
        self.assertFalse(EmailChangeRequest.objects.exists())
        self.assertFalse(
            EmailLog.objects.filter(
                email_type=EMAIL_CHANGE_CONFIRM_TEMPLATE,
            ).exists()
        )

    def test_request_validates_and_normalizes_new_email(self):
        response = _post_json(
            self.client,
            self.url,
            {
                "new_email": "  New-Member@TEST.com  ",
                "current_password": "CorrectPass123!",
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        request_obj = EmailChangeRequest.objects.get()
        self.assertEqual(request_obj.old_email, "old-member@test.com")
        self.assertEqual(request_obj.new_email, "new-member@test.com")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old-member@test.com")
        log = EmailLog.objects.get(email_type=EMAIL_CHANGE_CONFIRM_TEMPLATE)
        self.assertEqual(log.user_id, self.user.pk)
        self.assertEqual(log.recipient_email, "new-member@test.com")

    def test_request_rejects_invalid_same_primary_and_other_alias_collisions(self):
        other = User.objects.create_user(email="taken@test.com")
        alias_owner = User.objects.create_user(email="alias-owner@test.com")
        EmailAlias.objects.create(user=alias_owner, email="relay@test.com")

        cases = [
            {"new_email": "not-an-email", "expected": "invalid_email"},
            {"new_email": "old-member@test.com", "expected": "invalid_email"},
            {"new_email": other.email, "expected": "invalid_email"},
            {"new_email": "relay@test.com", "expected": "invalid_email"},
        ]
        for case in cases:
            with self.subTest(case=case["new_email"]):
                cache.clear()
                response = _post_json(
                    self.client,
                    self.url,
                    {
                        "new_email": case["new_email"],
                        "current_password": "CorrectPass123!",
                    },
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["code"], case["expected"])
                self.assertIn(
                    response.json()["error"],
                    {
                        "Enter a valid email address.",
                        "Enter a different email from your current login email.",
                        "That email cannot be used for this account.",
                    },
                )

        self.assertFalse(EmailChangeRequest.objects.exists())

    def test_request_replaces_existing_pending_request(self):
        first, _first_token = request_email_change(
            self.user,
            "first-new@test.com",
            current_password="CorrectPass123!",
            send=False,
        )
        cache.clear()

        response = _post_json(
            self.client,
            self.url,
            {
                "new_email": "second-new@test.com",
                "current_password": "CorrectPass123!",
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        first.refresh_from_db()
        self.assertIsNotNone(first.invalidated_at)
        pending = active_email_change_request_for_user(self.user)
        self.assertEqual(pending.new_email, "second-new@test.com")
        self.assertEqual(
            EmailChangeRequest.objects.filter(
                confirmed_at__isnull=True,
                invalidated_at__isnull=True,
            ).count(),
            1,
        )

    def test_request_throttles_repeated_same_target_email(self):
        response = _post_json(
            self.client,
            self.url,
            {
                "new_email": "new-member@test.com",
                "current_password": "CorrectPass123!",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)

        second = _post_json(
            self.client,
            self.url,
            {
                "new_email": "new-member@test.com",
                "current_password": "CorrectPass123!",
            },
        )

        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "throttled")
        self.assertEqual(
            EmailLog.objects.filter(
                email_type=EMAIL_CHANGE_CONFIRM_TEMPLATE,
            ).count(),
            1,
        )


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
