"""Tests for studio user impersonation."""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import NewsletterSubscriber

User = get_user_model()


@tag('core')
class ImpersonateUserTest(TestCase):
    """Tests for the impersonate_user view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='testpass',
        )

    def test_impersonate_requires_staff(self):
        """Non-staff users cannot impersonate."""
        User.objects.create_user(
            email='regular@test.com', password='testpass',
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(f'/studio/impersonate/{self.target.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_impersonate_requires_post(self):
        """GET requests are not allowed."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get(f'/studio/impersonate/{self.target.pk}/')
        self.assertEqual(response.status_code, 405)

    def test_impersonate_requires_authentication(self):
        """Anonymous users are redirected to login."""
        response = self.client.post(f'/studio/impersonate/{self.target.pk}/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_impersonate_logs_in_as_target(self):
        """After impersonation, the session user is the target."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post(f'/studio/impersonate/{self.target.pk}/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')
        # Verify the session now has the target user
        response = self.client.get('/')
        self.assertEqual(response.wsgi_request.user, self.target)

    def test_impersonate_stores_admin_id_in_session(self):
        """The admin's user ID is stored in the session for later restoration."""
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        session = self.client.session
        self.assertEqual(session['_impersonator_id'], self.staff.pk)

    def test_impersonate_nonexistent_user_returns_404(self):
        """Impersonating a nonexistent user returns 404."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/impersonate/99999/')
        self.assertEqual(response.status_code, 404)


@tag('core')
class StopImpersonationTest(TestCase):
    """Tests for the stop_impersonation view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='testpass',
        )

    def test_stop_impersonation_restores_admin(self):
        """Stopping impersonation logs back in as the original admin."""
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        # Verify we are the target
        response = self.client.get('/')
        self.assertEqual(response.wsgi_request.user, self.target)
        # Stop impersonation
        response = self.client.post('/studio/impersonate/stop/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('subscribers', response.url)
        # Verify we are back to admin
        response = self.client.get('/')
        self.assertEqual(response.wsgi_request.user, self.staff)

    def test_stop_impersonation_clears_session_key(self):
        """After stopping, _impersonator_id is removed from session."""
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        self.client.post('/studio/impersonate/stop/')
        self.assertNotIn('_impersonator_id', self.client.session)

    def test_stop_impersonation_requires_post(self):
        """GET requests are not allowed."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/impersonate/stop/')
        self.assertEqual(response.status_code, 405)

    def test_stop_without_impersonation_redirects(self):
        """Stopping when not impersonating just redirects to subscribers."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/impersonate/stop/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('subscribers', response.url)


class ImpersonationBannerTest(TestCase):
    """Tests for the impersonation banner in base.html."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='testpass',
        )

    def test_banner_shown_during_impersonation(self):
        """The yellow banner is visible when impersonating a user."""
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        response = self.client.get('/')
        self.assertContains(response, 'You are logged in as target@test.com')
        self.assertContains(response, 'Return to your account')

    def test_banner_not_shown_normally(self):
        """The banner is not visible during normal browsing."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/')
        self.assertNotContains(response, 'Return to your account')

    def test_banner_is_not_sticky(self):
        """Banner sits in normal flow (no sticky positioning) so it pushes content down."""
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        response = self.client.get('/')
        content = response.content.decode()
        # Locate the banner div and verify its classes do not include sticky/z-[60].
        banner_marker = 'You are logged in as'
        idx = content.find(banner_marker)
        self.assertNotEqual(idx, -1, 'impersonation banner not rendered')
        # Grab ~400 chars before the marker (the surrounding div)
        window = content[max(0, idx - 400):idx]
        self.assertNotIn('sticky top-0', window)
        self.assertNotIn('z-[60]', window)

    def test_header_always_at_top_in_markup(self):
        """Fixed header markup uses `top-0` regardless of impersonation.

        While impersonating, a small inline script offsets the header by
        the banner height *only when scrolled to the top of the page* so
        there is no white gap once the user scrolls past the banner. The
        scroll-driven behavior is JS, so the rendered HTML always has
        `fixed top-0` on the header. The script is verified via the
        presence of the `impersonation-banner` element it depends on.
        """
        # Not impersonating: header is top-0 and no JS offset script runs.
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'fixed top-0')
        self.assertNotContains(response, 'id="impersonation-banner"')

        # Impersonating: header is still `top-0` in markup, plus the
        # banner element with the id the inline script targets is present.
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        response = self.client.get('/')
        self.assertContains(response, 'fixed top-0')
        self.assertContains(response, 'id="impersonation-banner"')
        self.assertContains(response, 'id="site-header"')


class HeaderStopImpersonatingButtonTest(TestCase):
    """Tests for the 'Stop impersonating' button in the header user area."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='testpass',
        )

    def test_header_button_shown_during_impersonation(self):
        """Header shows a 'Stop impersonating' button while impersonating."""
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        response = self.client.get('/')
        self.assertContains(response, 'data-testid="header-stop-impersonating"')
        self.assertContains(response, 'Stop impersonating')

    def test_header_button_hidden_normally(self):
        """Header does not show the 'Stop impersonating' button when not impersonating."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="header-stop-impersonating"')
        self.assertNotContains(response, 'Stop impersonating')


class SubscriberListLoginAsButtonTest(TestCase):
    """Tests for the 'Login as' button on the subscribers page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.registered_user = User.objects.create_user(
            email='registered@test.com', password='testpass',
        )
        cls.sub_with_account = NewsletterSubscriber.objects.create(
            email='registered@test.com', is_active=True,
        )
        cls.sub_without_account = NewsletterSubscriber.objects.create(
            email='nouser@test.com', is_active=True,
        )

    def test_login_as_button_shown_for_subscriber_with_account(self):
        """The 'Login as' button appears for subscribers who have a user account."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/subscribers/')
        self.assertContains(response, f'/studio/impersonate/{self.registered_user.pk}/')

    def test_login_as_button_hidden_for_subscriber_without_account(self):
        """The 'Login as' button does not appear for email-only subscribers."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/subscribers/')
        # The subscriber without an account should not have a Login as button
        content = response.content.decode()
        # Check that there's no impersonate URL for a nonexistent user
        # We verify by counting the number of "Login as" buttons -- should be exactly 1
        self.assertEqual(content.count('Login as</button>'), 1)
