"""Tests for studio user impersonation."""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

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
        self.assertEqual(response.url, '/studio/users/')
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
        """Stopping when not impersonating just redirects to the users page."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/impersonate/stop/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/studio/users/')


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
    """The duplicate 'Stop impersonating' button has been removed from the
    header dropdown and mobile menu (issue #276). The banner's 'Return to
    your account' link is the single, canonical control.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='testpass',
        )

    def test_no_duplicate_stop_button_in_header_during_impersonation(self):
        """While impersonating, the header must not render the duplicate
        'Stop impersonating' button. Only the banner's 'Return to your
        account' link should be present.
        """
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post(f'/studio/impersonate/{self.target.pk}/')
        response = self.client.get('/')
        # Banner control is the single source of truth.
        self.assertContains(response, 'Return to your account')
        # Duplicate header button must be gone.
        self.assertNotContains(response, 'data-testid="header-stop-impersonating"')
        self.assertNotContains(response, 'Stop impersonating')

    def test_no_stop_button_when_not_impersonating(self):
        """The header has no 'Stop impersonating' button when not impersonating."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="header-stop-impersonating"')
        self.assertNotContains(response, 'Stop impersonating')


class UserListLoginAsButtonTest(TestCase):
    """The 'Login as' button on /studio/users/ works for any User row."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )
        cls.subscriber_user = User.objects.create_user(
            email='registered@test.com', password='testpass',
            unsubscribed=False,
        )
        cls.non_subscriber_user = User.objects.create_user(
            email='nosub@test.com', password='testpass',
            unsubscribed=True,
        )

    def test_login_as_button_present_for_subscriber_user(self):
        """A subscriber-User shows a Login as button on the default chip."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/users/')
        self.assertContains(
            response, f'/studio/impersonate/{self.subscriber_user.pk}/'
        )

    def test_login_as_button_present_for_any_user(self):
        """Login as also works for non-subscriber Users (was missing before #271)."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/users/?filter=all')
        self.assertContains(
            response, f'/studio/impersonate/{self.non_subscriber_user.pk}/'
        )

    def test_subscriber_filter_uses_user_newsletter_state(self):
        """The subscriber chip is backed by User.unsubscribed."""
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.get('/studio/users/?filter=subscribers')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertIn('registered@test.com', emails)
        self.assertNotIn('nosub@test.com', emails)
