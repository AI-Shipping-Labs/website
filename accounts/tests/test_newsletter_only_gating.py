"""Tests for newsletter-only UI gating (issue #769).

Coverage:

- ``is_newsletter_only_user`` predicate returns the expected value for
  each (auth, signup_source, account_activated) combination.
- The accounts context processor exposes ``is_newsletter_only`` to
  every template.
- ``/`` (authenticated) redirects newsletter-only users to ``/account/``
  with a one-shot info message; the dashboard renders normally for
  every other user shape.
- ``/account/`` for a newsletter-only user renders the trimmed page
  (CTA visible, profile/Slack/membership/timezone/change-password/
  account-info hidden) and the full page for everyone else.
- Header (desktop + mobile) hides the notification bell, Profile link
  and Plan link for newsletter-only users; staff retain Studio.
- Activating a newsletter-only user (via the password-reset API or
  ``mark_activated``) immediately re-renders the full dashboard + full
  navbar on the next request.
"""


import jwt
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, tag
from django.urls import reverse

from accounts.context_processors import newsletter_only_user
from accounts.gating import is_newsletter_only_user
from accounts.models import User


@tag('core')
class IsNewsletterOnlyUserPredicateTest(TestCase):
    """Truth table for ``is_newsletter_only_user``."""

    def test_anonymous_user_is_not_newsletter_only(self):
        self.assertFalse(is_newsletter_only_user(AnonymousUser()))

    def test_none_user_is_not_newsletter_only(self):
        self.assertFalse(is_newsletter_only_user(None))

    def test_newsletter_source_not_activated_is_newsletter_only(self):
        user = User.objects.create_user(
            email='nl@test.com',
            signup_source='newsletter',
        )
        self.assertTrue(is_newsletter_only_user(user))

    def test_newsletter_source_but_activated_is_not_newsletter_only(self):
        """Activation flag overrides signup_source — full UI returns."""
        user = User.objects.create_user(
            email='nl-active@test.com',
            signup_source='newsletter',
            account_activated=True,
        )
        self.assertFalse(is_newsletter_only_user(user))

    def test_signup_source_not_activated_is_not_newsletter_only(self):
        """Signed-up-but-never-did-anything users are NOT gated."""
        user = User.objects.create_user(
            email='signup@test.com',
            signup_source='signup',
        )
        self.assertFalse(is_newsletter_only_user(user))

    def test_oauth_source_is_not_newsletter_only(self):
        user = User.objects.create_user(
            email='oauth@test.com',
            signup_source='oauth',
        )
        self.assertFalse(is_newsletter_only_user(user))

    def test_imported_source_is_not_newsletter_only(self):
        user = User.objects.create_user(
            email='imp@test.com',
            signup_source='imported',
        )
        self.assertFalse(is_newsletter_only_user(user))

    def test_unknown_source_is_not_newsletter_only(self):
        """Pre-existing rows at the default ``unknown`` source see the full UI."""
        user = User.objects.create_user(email='unk@test.com')
        # default is 'unknown'
        self.assertEqual(user.signup_source, 'unknown')
        self.assertFalse(is_newsletter_only_user(user))

    def test_staff_does_not_override_predicate(self):
        """Staff with the (unlikely) newsletter source still hit the gate.

        The spec calls out the staff override on the Studio LINK only,
        not on the predicate itself — staff who land in this anomalous
        state should still see the trimmed UI (with Studio surfaced).
        """
        user = User.objects.create_user(
            email='staff@test.com',
            signup_source='newsletter',
            is_staff=True,
        )
        self.assertTrue(is_newsletter_only_user(user))


@tag('core')
class NewsletterOnlyContextProcessorTest(TestCase):
    """The accounts context processor exposes ``is_newsletter_only``."""

    def test_anonymous_request_returns_false(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        ctx = newsletter_only_user(request)
        self.assertIn('is_newsletter_only', ctx)
        self.assertFalse(ctx['is_newsletter_only'])

    def test_newsletter_only_user_request_returns_true(self):
        user = User.objects.create_user(
            email='cp-nl@test.com',
            signup_source='newsletter',
        )
        request = RequestFactory().get('/')
        request.user = user
        ctx = newsletter_only_user(request)
        self.assertTrue(ctx['is_newsletter_only'])

    def test_activated_user_request_returns_false(self):
        user = User.objects.create_user(
            email='cp-active@test.com',
            signup_source='newsletter',
            account_activated=True,
        )
        request = RequestFactory().get('/')
        request.user = user
        ctx = newsletter_only_user(request)
        self.assertFalse(ctx['is_newsletter_only'])


@tag('core')
class HomeRedirectForNewsletterOnlyUserTest(TestCase):
    """``GET /`` redirects newsletter-only users to ``/account/``."""

    def test_newsletter_only_user_redirected_to_account(self):
        user = User.objects.create_user(
            email='home-nl@test.com',
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/account/')

    def test_redirect_carries_info_message(self):
        user = User.objects.create_user(
            email='home-msg@test.com',
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        # follow=True drains the message on the second render.
        response = self.client.get('/', follow=True)
        # Message is one-shot on the next request; collect from
        # context.
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('newsletter', str(messages[0]).lower())
        self.assertIn('password', str(messages[0]).lower())

    def test_signup_source_user_not_activated_sees_dashboard(self):
        user = User.objects.create_user(
            email='home-su@test.com',
            password='x' * 16,
            signup_source='signup',
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/')
        # Dashboard renders (200), not a redirect.
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')

    def test_activated_newsletter_user_sees_dashboard(self):
        """``signup_source='newsletter'`` + ``account_activated=True``
        is not gated — they get the dashboard."""
        user = User.objects.create_user(
            email='home-anl@test.com',
            password='x' * 16,
            signup_source='newsletter',
            account_activated=True,
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')

    def test_anonymous_visitor_sees_public_home(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')


@tag('core')
class AccountPageGatedForNewsletterOnlyUserTest(TestCase):
    """``/account/`` is trimmed for newsletter-only users."""

    def _login_newsletter_only(self, email='acct-nl@test.com'):
        user = User.objects.create_user(
            email=email,
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        return user

    def test_newsletter_only_renders_cta_card(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'newsletter-only-cta')
        self.assertContains(response, 'Set a password to do more')

    def test_newsletter_only_hides_profile_form(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        # The Profile section's specific id is gone for newsletter-only.
        self.assertNotContains(response, 'id="profile-section"')
        self.assertNotContains(response, 'id="profile-form"')

    def test_newsletter_only_hides_slack_card(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        # The Slack join CTA pulled from the shared partial.
        self.assertNotContains(response, 'data-testid="slack-account-card"')

    def test_newsletter_only_hides_membership_section(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        # The tier name span only exists in the Membership card.
        self.assertNotContains(response, 'id="tier-name"')

    def test_newsletter_only_hides_change_password_section(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertNotContains(response, 'id="change-password-section"')

    def test_newsletter_only_hides_display_preferences(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertNotContains(response, 'id="display-preferences-section"')
        self.assertNotContains(response, 'id="timezone-preference-input"')

    def test_newsletter_only_hides_account_info(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertNotContains(response, 'id="account-info-section"')

    def test_newsletter_only_keeps_email_preferences(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertContains(response, 'id="email-preferences-section"')
        self.assertContains(response, 'id="newsletter-toggle"')
        self.assertContains(response, 'id="workshop-emails-toggle"')

    def test_cta_links_to_password_reset_with_email(self):
        user = self._login_newsletter_only(email='cta@test.com')
        response = self.client.get('/account/')
        self.assertContains(
            response,
            'href="/accounts/password-reset-request?email=cta%40test.com"',
        )
        # Sanity: also confirms the email is rendered on the card.
        self.assertContains(response, user.email)

    def test_full_account_page_renders_for_signup_source_user(self):
        user = User.objects.create_user(
            email='full-signup@test.com',
            password='x' * 16,
            signup_source='signup',
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/account/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="profile-section"')
        self.assertContains(response, 'id="change-password-section"')
        self.assertContains(response, 'id="display-preferences-section"')
        self.assertContains(response, 'id="account-info-section"')
        self.assertNotContains(response, 'id="newsletter-only-cta"')

    def test_full_account_page_renders_for_activated_newsletter_user(self):
        user = User.objects.create_user(
            email='full-anl@test.com',
            password='x' * 16,
            signup_source='newsletter',
            account_activated=True,
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/account/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="profile-section"')
        self.assertNotContains(response, 'id="newsletter-only-cta"')


@tag('core')
class HeaderHidesPlatformItemsForNewsletterOnlyUserTest(TestCase):
    """Navbar trims notification bell, Profile and Plan items."""

    def _login_newsletter_only(self):
        user = User.objects.create_user(
            email='hdr-nl@test.com',
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        return user

    def test_notification_bell_not_rendered(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertNotContains(response, 'id="notification-bell-btn"')
        self.assertNotContains(response, 'id="notification-bell-container"')

    def test_profile_menu_item_not_rendered(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        # Account-dropdown Profile link href ends with #profile.
        self.assertNotContains(response, 'href="/account/#profile"')

    def test_account_link_still_rendered(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        # Account is the one menu item the gating intentionally keeps.
        self.assertContains(response, 'id="account-menu-trigger"')
        self.assertContains(response, 'data-testid="account-menu-dropdown"')

    def test_log_out_still_rendered(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertContains(response, 'Log out')

    def test_mobile_notifications_link_not_rendered(self):
        self._login_newsletter_only()
        response = self.client.get('/account/')
        self.assertNotContains(
            response, 'data-testid="mobile-notifications-link"'
        )

    def test_full_navbar_for_signup_source_user(self):
        user = User.objects.create_user(
            email='hdr-su@test.com',
            password='x' * 16,
            signup_source='signup',
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/account/')
        self.assertContains(response, 'id="notification-bell-btn"')
        self.assertContains(response, 'href="/account/#profile"')

    def test_staff_with_newsletter_source_still_sees_studio(self):
        """Staff override on the Studio link survives the gating."""
        user = User.objects.create_user(
            email='hdr-staff@test.com',
            password='x' * 16,
            signup_source='newsletter',
            is_staff=True,
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/account/')
        # Studio link is still rendered.
        self.assertContains(response, "{}".format(reverse('studio_dashboard')))
        # But notification bell is hidden, confirming the gate fires.
        self.assertNotContains(response, 'id="notification-bell-btn"')


@tag('core')
class ActivationRevealsFullUITest(TestCase):
    """Once activated, the next request shows the full UI."""

    def test_password_reset_flips_account_activated(self):
        """``password_reset_api`` POST calls ``mark_activated``."""
        user = User.objects.create_user(
            email='reset@test.com',
            password='old' * 4,
            signup_source='newsletter',
            email_verified=True,
        )
        self.assertFalse(user.account_activated)

        # Generate a valid reset token.
        import datetime as dt
        payload = {
            'user_id': user.pk,
            'action': 'password_reset',
            'exp': dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        }
        token = jwt.encode(
            payload, settings.SECRET_KEY, algorithm='HS256',
        )

        response = self.client.post(
            '/api/password-reset',
            data='{"token": "%s", "new_password": "newpassword123"}' % token,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.account_activated)

    def test_dashboard_renders_after_activation_within_same_session(self):
        """Mid-session activation: next ``/`` request shows the dashboard."""
        user = User.objects.create_user(
            email='live-act@test.com',
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        # Pre-activation: redirect to /account/
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/account/')

        # Activate the user out-of-band.
        from accounts.utils.activation import mark_activated
        mark_activated(user)

        # Post-activation: dashboard renders.
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')

    def test_navbar_re_renders_full_after_activation(self):
        user = User.objects.create_user(
            email='nav-act@test.com',
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        # Pre-activation: bell hidden.
        response = self.client.get('/account/')
        self.assertNotContains(response, 'id="notification-bell-btn"')

        from accounts.utils.activation import mark_activated
        mark_activated(user)

        # Post-activation: bell back.
        response = self.client.get('/account/')
        self.assertContains(response, 'id="notification-bell-btn"')


@tag('core')
class PasswordResetRequestEmailPrefillTest(TestCase):
    """``?email=`` querystring pre-fills the password-reset-request form."""

    def test_querystring_pre_fills_email_input(self):
        response = self.client.get(
            '/accounts/password-reset-request?email=alice%40example.com'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="alice@example.com"')

    def test_no_querystring_leaves_value_empty(self):
        response = self.client.get('/accounts/password-reset-request')
        self.assertEqual(response.status_code, 200)
        # The input renders with an empty value attr.
        self.assertContains(response, 'value=""')

    def test_authenticated_user_with_querystring_still_sees_form(self):
        """The newsletter-only CTA must work even though the user is logged in."""
        user = User.objects.create_user(
            email='pr-auth@test.com',
            password='x' * 16,
            signup_source='newsletter',
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get(
            '/accounts/password-reset-request?email=pr-auth%40test.com'
        )
        # Renders the form (not the /account/ redirect).
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'password-reset-request-form')

    def test_authenticated_user_without_querystring_redirects_to_account(self):
        """Pre-existing behaviour: authed visit with no querystring still bounces."""
        user = User.objects.create_user(
            email='pr-redir@test.com',
            password='x' * 16,
            signup_source='signup',
            email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get('/accounts/password-reset-request')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/account/')
