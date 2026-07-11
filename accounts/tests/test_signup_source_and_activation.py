"""Tests for ``signup_source`` and ``account_activated`` (issue #768).

Coverage:

- Every production ``User.objects.create_user`` entry point stamps the
  expected ``signup_source`` value (newsletter / signup / imported /
  staff_create / oauth) and the right ``account_activated`` initial
  bit (only Stripe checkout and OAuth signal land True at creation).
- ``mark_activated`` is idempotent.
- Activation triggers (verify-email for signup-source, comment post,
  authenticated event registration, course unit completion, sprint
  plan creation, Slack OAuth membership flip) all flip False → True.
- Newsletter subscribers that verify but take no other action stay
  inactive.
- The pre-existing-row default (``signup_source='unknown'``,
  ``account_activated=False``) is preserved when no migration backfill
  ran.
"""

import datetime
import json
from unittest.mock import MagicMock, patch

import jwt
from django.conf import settings
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from accounts.utils.activation import mark_activated, mark_email_verified
from email_app.models import EmailLog


@tag('core')
class MarkActivatedHelperTest(TestCase):
    """``mark_activated`` is idempotent and only writes when flipping."""

    def test_flips_false_to_true(self):
        user = User.objects.create_user(
            email='m1@test.com',
            signup_source='signup',
        )
        self.assertFalse(user.account_activated)
        flipped = mark_activated(user)
        self.assertTrue(flipped)
        user.refresh_from_db()
        self.assertTrue(user.account_activated)

    def test_idempotent_no_op_when_already_true(self):
        user = User.objects.create_user(
            email='m2@test.com',
            signup_source='signup',
            account_activated=True,
        )
        with patch.object(User, 'save') as mock_save:
            flipped = mark_activated(user)
        self.assertFalse(flipped)
        mock_save.assert_not_called()

    def test_safe_on_unsaved_user(self):
        """A User with no PK is a no-op, not a crash."""
        user = User(email='m3@test.com')
        self.assertIsNone(user.pk)
        self.assertFalse(mark_activated(user))

    def test_safe_on_none(self):
        self.assertFalse(mark_activated(None))


class MarkEmailVerifiedHelperTest(TestCase):
    """Issue #839: ``mark_email_verified`` is idempotent, writes once."""

    def test_flips_false_to_true(self):
        user = User.objects.create_user(
            email='v1@test.com',
            email_verified=False,
        )
        self.assertTrue(mark_email_verified(user))
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_idempotent_no_op_when_already_verified(self):
        user = User.objects.create_user(
            email='v2@test.com',
            email_verified=True,
        )
        with patch.object(User, 'save') as mock_save:
            flipped = mark_email_verified(user)
        self.assertFalse(flipped)
        mock_save.assert_not_called()

    def test_safe_on_unsaved_user(self):
        user = User(email='v3@test.com')
        self.assertIsNone(user.pk)
        self.assertFalse(mark_email_verified(user))

    def test_safe_on_none(self):
        self.assertFalse(mark_email_verified(None))


@tag('core')
class NewsletterSubscribeSignupSourceTest(TestCase):
    """``/api/subscribe`` stamps ``signup_source='newsletter'``."""

    def test_new_subscriber_gets_newsletter_source_and_stays_inactive(self):
        with patch('email_app.views.newsletter._send_subscribe_verification_email'):
            response = self.client.post(
                '/api/subscribe',
                data=json.dumps({'email': 'news@test.com'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email='news@test.com')
        self.assertEqual(user.signup_source, 'newsletter')
        self.assertFalse(user.account_activated)
        self.assertFalse(user.email_verified)


@tag('core')
class RegisterApiSignupSourceTest(TestCase):
    """``/api/register`` stamps ``signup_source='signup'``."""

    def test_new_signup_gets_signup_source_and_stays_inactive(self):
        with patch('accounts.views.auth._send_verification_email'), \
             patch('accounts.views.auth._probe_slack_membership_on_signup'):
            response = self.client.post(
                '/api/register',
                data=json.dumps({
                    'email': 'reg@test.com',
                    'password': 'longenoughpw',
                }),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='reg@test.com')
        self.assertEqual(user.signup_source, 'signup')
        self.assertFalse(user.account_activated)


@tag('core')
class VerifyEmailActivatesSignupSourceTest(TestCase):
    """``verify_email_api`` flips ``account_activated`` for signup-source users only."""

    def _make_verify_token(self, user_id):
        payload = {
            'user_id': user_id,
            'action': 'verify_email',
            'exp': datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

    def test_signup_source_user_activates_on_verify(self):
        user = User.objects.create_user(
            email='vsignup@test.com',
            password='longenoughpw',
            signup_source='signup',
        )
        self.assertFalse(user.account_activated)
        token = self._make_verify_token(user.pk)
        response = self.client.get(f'/api/verify-email?token={token}')
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertTrue(user.account_activated)

    def test_newsletter_source_user_stays_inactive_on_verify(self):
        """Newsletter subscribers that verify do NOT get activated."""
        user = User.objects.create_user(
            email='vnews@test.com',
            signup_source='newsletter',
        )
        token = self._make_verify_token(user.pk)
        response = self.client.get(f'/api/verify-email?token={token}')
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertFalse(user.account_activated)


@tag('core')
class StaffCreateSignupSourceTest(TestCase):
    """Studio user create stamps ``signup_source='staff_create'`` and activates."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='admin@test.com',
            password='longenoughpw',
        )
        self.client.force_login(self.admin)

    def test_studio_user_create_marks_staff_create_and_activated(self):
        response = self.client.post(
            '/studio/users/new/',
            data={'email': 'made@test.com'},
        )
        # Studio user_create redirects to the confirmation page on success.
        self.assertIn(response.status_code, (302, 303))
        user = User.objects.get(email='made@test.com')
        self.assertEqual(user.signup_source, 'staff_create')
        self.assertTrue(user.account_activated)


@tag('core')
class ImportUsersSignupSourceTest(TestCase):
    """``run_import_batch`` stamps ``signup_source='imported'`` on new rows."""

    def test_create_via_run_import_batch(self):
        from accounts.services.import_users import ImportRow, run_import_batch

        def rows():
            yield ImportRow(email='imp1@test.com', name='Ada')

        batch = run_import_batch('slack', rows, send_welcome=False)
        self.assertEqual(batch.users_created, 1)
        user = User.objects.get(email='imp1@test.com')
        self.assertEqual(user.signup_source, 'imported')


@tag('core')
class ContactsImportSignupSourceTest(TestCase):
    """``studio/services/contacts_import`` stamps ``signup_source='imported``."""

    def test_create_via_contacts_import(self):
        from studio.services.contacts_import import import_contact_rows

        rows = [{'email': 'contact1@test.com'}]
        result = import_contact_rows(rows, default_tag='vip')
        self.assertEqual(result.created, 1)
        user = User.objects.get(email='contact1@test.com')
        self.assertEqual(user.signup_source, 'imported')


@tag('core')
class CommentActivationTest(TestCase):
    """Posting a comment flips ``account_activated`` False → True."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='cm@test.com',
            password='longenoughpw',
            signup_source='signup',
            email_verified=True,
            account_activated=False,
        )
        self.client.force_login(self.user)

    def test_create_comment_activates(self):
        content_id = '00000000-0000-0000-0000-000000000001'
        response = self.client.post(
            f'/api/comments/{content_id}',
            data=json.dumps({'body': 'hello'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertTrue(self.user.account_activated)


@tag('core')
class EventRegistrationActivationTest(TestCase):
    """Authenticated event registration flips ``account_activated``."""

    def setUp(self):
        from events.models import Event

        self.user = User.objects.create_user(
            email='ev@test.com',
            password='longenoughpw',
            signup_source='signup',
            email_verified=True,
            account_activated=False,
        )
        self.event = Event.objects.create(
            slug='party',
            title='Party',
            description='x',
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            end_datetime=timezone.now() + datetime.timedelta(days=2, hours=1),
            status='upcoming',
            required_level=0,
        )
        self.client.force_login(self.user)

    def test_authenticated_register_activates(self):
        response = self.client.post(f'/api/events/{self.event.slug}/register')
        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertTrue(self.user.account_activated)

    def test_authenticated_register_idempotent_when_already_active(self):
        # Pre-activate from another action.
        self.user.account_activated = True
        self.user.save(update_fields=['account_activated'])

        response = self.client.post(f'/api/events/{self.event.slug}/register')
        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertTrue(self.user.account_activated)


@tag('core')
class CourseUnitCompletionActivationTest(TestCase):
    """Marking a course unit complete flips ``account_activated``."""

    def setUp(self):
        from content.models import Course, Module, Unit

        self.user = User.objects.create_user(
            email='cu@test.com',
            password='longenoughpw',
            signup_source='signup',
            email_verified=True,
            account_activated=False,
        )
        self.course = Course.objects.create(
            slug='c1',
            title='C1',
            description='x',
            status='published',
            required_level=0,
        )
        self.module = Module.objects.create(
            course=self.course, slug='m1', title='M1', sort_order=0,
        )
        self.unit = Unit.objects.create(
            module=self.module,
            slug='u1',
            title='U1',
            sort_order=0,
            is_preview=True,  # bypass tier check
        )
        self.client.force_login(self.user)

    def test_complete_unit_activates(self):
        response = self.client.post(
            f'/api/courses/{self.course.slug}/units/{self.unit.pk}/complete',
        )
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'completed': True})
        self.user.refresh_from_db()
        self.assertTrue(self.user.account_activated)


@tag('core')
class SprintPlanCreationActivationTest(TestCase):
    """Creating a sprint plan flips ``account_activated``."""

    def test_plan_creation_activates(self):
        from plans.models import Sprint
        from plans.services import create_plan_for_enrollment

        user = User.objects.create_user(
            email='sp@test.com',
            password='longenoughpw',
            signup_source='signup',
            account_activated=False,
        )
        sprint = Sprint.objects.create(
            slug='s1', name='Sprint 1', duration_weeks=2,
            start_date=timezone.now().date(),
            status='active',
            min_tier_level=0,
        )
        plan, _enr, created = create_plan_for_enrollment(
            sprint=sprint, user=user, enrolled_by=user,
        )
        self.assertTrue(created)
        user.refresh_from_db()
        self.assertTrue(user.account_activated)


@tag('core')
class OAuthSocialAccountAddedSignalTest(TestCase):
    """``social_account_added`` stamps oauth + activates for placeholder rows.

    Existing users with a non-``unknown`` source keep their original source
    (only ``account_activated`` is flipped to True).
    """

    def _trigger_signal(self, user):
        from allauth.socialaccount.signals import social_account_added

        sociallogin = MagicMock()
        sociallogin.user = user
        sociallogin.account = MagicMock()
        sociallogin.account.provider = 'google'
        sociallogin.account.extra_data = {}
        social_account_added.send(
            sender=type(user),
            request=None,
            sociallogin=sociallogin,
        )

    def test_brand_new_oauth_user_gets_oauth_source_and_activated(self):
        # Fresh row: signup_source defaults to 'unknown' (allauth flow
        # would call save_user() before social_account_added fires).
        user = User.objects.create_user(email='oauth@test.com')
        self.assertEqual(user.signup_source, 'unknown')

        self._trigger_signal(user)

        user.refresh_from_db()
        self.assertEqual(user.signup_source, 'oauth')
        self.assertTrue(user.account_activated)

    def test_brand_new_oauth_user_gets_one_free_welcome(self):
        user = User.objects.create_user(email='oauth-welcome@test.com')
        self.assertEqual(user.signup_source, 'unknown')

        self._trigger_signal(user)

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertTrue(user.account_activated)
        self.assertEqual(user.signup_source, 'oauth')
        self.assertEqual(
            EmailLog.objects.filter(
                user=user,
                email_type='free_welcome',
            ).count(),
            1,
        )

        self._trigger_signal(user)
        self.assertEqual(
            EmailLog.objects.filter(
                user=user,
                email_type='free_welcome',
            ).count(),
            1,
        )

    def test_existing_newsletter_user_linking_oauth_keeps_original_source(self):
        user = User.objects.create_user(
            email='news+oauth@test.com',
            signup_source='newsletter',
            account_activated=False,
        )

        self._trigger_signal(user)

        user.refresh_from_db()
        # Source stays 'newsletter' — only 'unknown' placeholders get
        # promoted by the oauth signal.
        self.assertEqual(user.signup_source, 'newsletter')
        # But OAuth is an activation event.
        self.assertTrue(user.account_activated)
        self.assertFalse(
            EmailLog.objects.filter(
                user=user,
                email_type='free_welcome',
            ).exists()
        )

    def test_existing_user_linking_oauth_does_not_duplicate_free_welcome(self):
        user = User.objects.create_user(
            email='signup+oauth@test.com',
            signup_source='signup',
            account_activated=True,
            email_verified=True,
        )
        EmailLog.objects.create(user=user, email_type='free_welcome')

        self._trigger_signal(user)

        user.refresh_from_db()
        self.assertEqual(user.signup_source, 'signup')
        self.assertEqual(
            EmailLog.objects.filter(
                user=user,
                email_type='free_welcome',
            ).count(),
            1,
        )


@tag('core')
class OAuthSignupGtagSessionFlagTest(TestCase):
    """Issue #774: brand-new OAuth signup stashes a one-shot gtag_event_pending."""

    def _trigger_signal(self, user, request, provider='google'):
        from allauth.socialaccount.signals import social_account_added

        sociallogin = MagicMock()
        sociallogin.user = user
        sociallogin.account = MagicMock()
        sociallogin.account.provider = provider
        sociallogin.account.extra_data = {}
        social_account_added.send(
            sender=type(user),
            request=request,
            sociallogin=sociallogin,
        )

    def _request_with_session(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def test_brand_new_oauth_signup_sets_pending_sign_up_event(self):
        user = User.objects.create_user(email='oauthflag@test.com')
        self.assertEqual(user.signup_source, 'unknown')
        request = self._request_with_session()

        self._trigger_signal(user, request, provider='google')

        self.assertEqual(
            request.session.get('gtag_event_pending'),
            {
                'event': 'sign_up',
                'params': {
                    'method': 'oauth',
                    'provider': 'google',
                    'signup_kind': 'account',
                    'login_state': 'authenticated',
                },
            },
        )

    def test_existing_user_linking_oauth_does_not_set_pending_event(self):
        # An existing email-signup user later adding a Google provider
        # is NOT a fresh sign_up — the GA event must not fire.
        user = User.objects.create_user(
            email='existing+oauth@test.com',
            signup_source='signup',
            account_activated=False,
        )
        request = self._request_with_session()

        self._trigger_signal(user, request, provider='google')

        self.assertNotIn('gtag_event_pending', request.session)

    def test_returning_oauth_login_does_not_set_pending_event(self):
        # A returning OAuth login uses allauth's existing-social-account path,
        # not social_account_added, so it must not create a new conversion.
        from allauth.socialaccount.models import SocialAccount, SocialLogin
        from allauth.socialaccount.signals import pre_social_login

        user = User.objects.create_user(
            email='returning+oauth@test.com',
            signup_source='oauth',
            account_activated=True,
            email_verified=True,
        )
        account = SocialAccount.objects.create(
            user=user,
            provider='google',
            uid='google-returning-uid',
        )
        request = self._request_with_session()
        sociallogin = SocialLogin(
            user=user,
            account=account,
            email_addresses=[],
        )

        self.assertTrue(sociallogin.is_existing)
        pre_social_login.send(
            sender=None,
            request=request,
            sociallogin=sociallogin,
        )

        self.assertNotIn('gtag_event_pending', request.session)

    def test_no_request_safe_no_crash(self):
        # Allauth may send the signal with request=None in some edge
        # paths — the handler must not crash and must skip the flag.
        user = User.objects.create_user(email='oauthnoreq@test.com')

        # Should not raise.
        self._trigger_signal(user, request=None, provider='google')

        user.refresh_from_db()
        # Source still gets promoted via the existing logic.
        self.assertEqual(user.signup_source, 'oauth')


@tag('core')
class SlackOAuthMembershipActivationTest(TestCase):
    """``_apply_slack_oauth_membership`` activates the user as a side effect."""

    def test_slack_membership_flip_activates(self):
        from accounts.signals import _apply_slack_oauth_membership

        user = User.objects.create_user(
            email='slack@test.com',
            signup_source='signup',
            account_activated=False,
        )

        sociallogin = MagicMock()
        sociallogin.account = MagicMock()
        sociallogin.account.extra_data = {
            'https://slack.com/user_id': 'U123',
        }

        _apply_slack_oauth_membership(user, sociallogin)

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        self.assertTrue(user.account_activated)


@tag('core')
class StripeCheckoutCompletedActivationTest(TestCase):
    """Stripe checkout activates the user and creates new ones as imported+active."""

    def setUp(self):
        from payments.models import Tier
        Tier.objects.get_or_create(
            slug='basic',
            defaults={
                'name': 'Basic',
                'level': 10,
                'is_active': True,
            },
        )

    def _session(self, email, subscription_id='sub_abc'):
        return {
            'id': 'cs_test',
            'customer': 'cus_test',
            'customer_details': {'email': email, 'name': ''},
            'subscription': subscription_id,
            'client_reference_id': None,
            'metadata': {'tier_slug': 'basic'},
        }

    @override_settings()
    def test_new_user_via_checkout_is_imported_and_activated(self):
        from payments.services.webhook_handlers import (
            handle_checkout_completed,
        )

        with patch(
            'payments.services._get_subscription_period_end', return_value=None,
        ), patch(
            'payments.services._get_subscription_price_id', return_value='',
        ), patch(
            'payments.services._record_conversion_attribution',
        ), patch(
            'payments.services._community_invite',
        ), patch(
            'payments.services.webhook_handlers._send_payment_notification_email',
        ):
            handle_checkout_completed(self._session('pay@test.com'))

        user = User.objects.get(email='pay@test.com')
        self.assertEqual(user.signup_source, 'imported')
        self.assertTrue(user.account_activated)

    def test_existing_user_checkout_activates_them(self):
        from payments.services.webhook_handlers import (
            handle_checkout_completed,
        )

        user = User.objects.create_user(
            email='upgrade@test.com',
            password='longenoughpw',
            signup_source='signup',
            account_activated=False,
        )

        with patch(
            'payments.services._get_subscription_period_end', return_value=None,
        ), patch(
            'payments.services._get_subscription_price_id', return_value='',
        ), patch(
            'payments.services._record_conversion_attribution',
        ), patch(
            'payments.services._community_invite',
        ), patch(
            'payments.services.webhook_handlers._send_payment_notification_email',
        ):
            handle_checkout_completed(self._session('upgrade@test.com'))

        user.refresh_from_db()
        # Source stays — they were not new — but activation flips.
        self.assertEqual(user.signup_source, 'signup')
        self.assertTrue(user.account_activated)


@tag('core')
class StudioUserDetailChipsTest(TestCase):
    """The Studio user detail page surfaces Source + Activated chips."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email='studio-admin@test.com',
            password='longenoughpw',
        )
        cls.signup_user = User.objects.create_user(
            email='chip-signup@test.com',
            signup_source='signup',
            account_activated=True,
        )
        cls.newsletter_user = User.objects.create_user(
            email='chip-news@test.com',
            signup_source='newsletter',
            account_activated=False,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_signup_active_user_renders_source_and_activated_chips(self):
        response = self.client.get(f'/studio/users/{self.signup_user.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-testid="user-detail-signup-source"',
        )
        self.assertContains(response, 'data-signup-source="signup"')
        self.assertContains(response, 'Email + password signup')
        self.assertContains(response, 'data-account-activated="yes"')
        self.assertContains(response, 'data-testid="user-detail-account-lifecycle"')
        self.assertContains(response, 'data-lifecycle="full_account"')
        self.assertContains(response, 'Full account')

    def test_inactive_newsletter_user_renders_chips_with_no_activation(self):
        response = self.client.get(f'/studio/users/{self.newsletter_user.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-signup-source="newsletter"')
        self.assertContains(response, 'Newsletter subscribe')
        self.assertContains(response, 'data-account-activated="no"')
        self.assertContains(response, 'data-lifecycle="newsletter_only"')
        self.assertContains(response, 'Newsletter-only')

    def test_activated_newsletter_user_reports_full_account_lifecycle(self):
        self.newsletter_user.account_activated = True
        self.newsletter_user.save(update_fields=['account_activated'])
        response = self.client.get(f'/studio/users/{self.newsletter_user.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-signup-source="newsletter"')
        self.assertContains(response, 'data-account-activated="yes"')
        self.assertContains(response, 'data-lifecycle="full_account"')

    def test_no_django_comment_leaks_on_user_detail(self):
        """Multi-line ``{# ... #}`` blocks leak — assert none ship to HTML."""
        response = self.client.get(f'/studio/users/{self.signup_user.pk}/')
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '#}')


@tag('core')
class PreExistingRowDefaultsTest(TestCase):
    """A row with the schema defaults stays unknown + inactive."""

    def test_pre_migration_row_stays_unknown_and_inactive(self):
        # Mimic the post-migration shape of a row that existed before
        # the columns were added: it picks up the field defaults
        # ('unknown' / False) and nothing in this test path touches
        # those bits.
        user = User.objects.create_user(email='legacy@test.com')
        # NOTE: this creation path goes through ``UserManager.create_user``
        # which does NOT pass ``signup_source``; the model default kicks in.
        self.assertEqual(user.signup_source, 'unknown')
        self.assertFalse(user.account_activated)

        user.refresh_from_db()
        self.assertEqual(user.signup_source, 'unknown')
        self.assertFalse(user.account_activated)
