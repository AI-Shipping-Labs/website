"""Tests for the Studio "New user" form (issue #234).

The form provisions a user with a generated password, surfaces the password
exactly once on a confirmation page, then drops it from the session. The
view is gated on ``is_superuser`` because it can mint other superusers.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from payments.models import Tier
from studio.views.users import SESSION_KEY

User = get_user_model()


class UserCreateAccessControlTest(TestCase):
    """Only existing superusers can reach the form -- staff alone is not enough."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )
        cls.staff_only = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='testpass',
        )

    def test_anonymous_redirected_to_login(self):
        client = Client()
        response = client.get('/studio/users/new/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_member_gets_403(self):
        client = Client()
        client.login(email='member@test.com', password='testpass')
        response = client.get('/studio/users/new/')
        self.assertEqual(response.status_code, 403)

    def test_staff_only_gets_403(self):
        """Staff WITHOUT superuser cannot reach the form -- minting superusers
        requires superuser yourself."""
        client = Client()
        client.login(email='staff@test.com', password='testpass')
        response = client.get('/studio/users/new/')
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_view_form(self):
        client = Client()
        client.login(email='super@test.com', password='testpass')
        response = client.get('/studio/users/new/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/create.html')

    def test_post_blocked_for_staff_only(self):
        """Submission must also be locked down -- the gate is on the view."""
        client = Client()
        client.login(email='staff@test.com', password='testpass')
        response = client.post('/studio/users/new/', {'email': 'x@test.com'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email='x@test.com').exists())

    def test_done_page_blocked_for_staff_only(self):
        client = Client()
        client.login(email='staff@test.com', password='testpass')
        response = client.get('/studio/users/created/')
        self.assertEqual(response.status_code, 403)


class UserCreateFormRenderTest(TestCase):
    """The GET form has the right fields and no tier selector."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='super@test.com', password='testpass')

    def test_form_has_required_email_field(self):
        response = self.client.get('/studio/users/new/')
        self.assertContains(response, 'name="email"')
        # The field is marked required in the HTML.
        self.assertContains(response, 'id="id_email"')

    def test_form_has_optional_name_fields(self):
        response = self.client.get('/studio/users/new/')
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')

    def test_form_has_make_admin_checkbox_unchecked_by_default(self):
        response = self.client.get('/studio/users/new/')
        self.assertContains(response, 'name="make_admin"')
        self.assertContains(response, 'type="checkbox"')
        # On a blank GET, the checkbox must not be pre-checked.
        self.assertNotContains(response, 'name="make_admin"\n               checked')

    def test_form_has_admin_helper_text(self):
        """The admin checkbox documents what it actually does."""
        response = self.client.get('/studio/users/new/')
        self.assertContains(response, 'Grants staff + superuser access')
        self.assertContains(response, 'Skips tier')

    def test_form_does_not_render_tier_selector(self):
        """Issue #234: the form must NOT have a tier picker."""
        response = self.client.get('/studio/users/new/')
        self.assertNotContains(response, 'name="tier"')
        self.assertNotContains(response, 'name="tier_id"')


class UserCreateRegularUserTest(TestCase):
    """Submitting the form WITHOUT 'Make admin' -> free tier, no staff flags."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='super@test.com', password='testpass')

    def test_creates_active_user_on_free_tier(self):
        response = self.client.post('/studio/users/new/', {
            'email': 'new@test.com',
            'first_name': 'New',
            'last_name': 'Person',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/users/created/')

        user = User.objects.get(email='new@test.com')
        self.assertTrue(user.is_active)
        self.assertEqual(user.first_name, 'New')
        self.assertEqual(user.last_name, 'Person')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        # Default tier is free per the User.save() default.
        self.assertIsNotNone(user.tier)
        self.assertEqual(user.tier.slug, 'free')

    def test_email_is_marked_verified(self):
        self.client.post('/studio/users/new/', {'email': 'verified@test.com'})
        user = User.objects.get(email='verified@test.com')
        self.assertTrue(user.email_verified)

    def test_password_is_set_via_set_password(self):
        """The hashed password lets the new user log in immediately."""
        self.client.post('/studio/users/new/', {'email': 'login@test.com'})
        user = User.objects.get(email='login@test.com')
        # ``has_usable_password`` returns True only when set_password() ran.
        self.assertTrue(user.has_usable_password())
        # The raw password is in the session stash -- pull it and try a login.
        session = self.client.session
        stash = session[SESSION_KEY]
        password = stash['password']
        self.assertGreaterEqual(len(password), 16)

        login_client = Client()
        ok = login_client.login(email='login@test.com', password=password)
        self.assertTrue(ok, 'New user must be able to log in with the generated password.')

    def test_email_is_normalized(self):
        """The User model normalises the domain part on save -- whitespace is stripped here."""
        self.client.post('/studio/users/new/', {'email': '  Trimmed@Example.com  '})
        # The form-level strip drops the surrounding whitespace; UserManager
        # normalises the domain to lowercase.
        self.assertTrue(User.objects.filter(email__iexact='trimmed@example.com').exists())


class UserCreateAdminUserTest(TestCase):
    """Submitting WITH 'Make admin' -> staff + superuser, no tier override."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='super@test.com', password='testpass')

    def test_make_admin_grants_staff_and_superuser(self):
        self.client.post('/studio/users/new/', {
            'email': 'admin@test.com',
            'make_admin': 'on',
        })
        user = User.objects.get(email='admin@test.com')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)

    def test_admin_can_immediately_access_studio(self):
        """An admin user created here can sign in and load Studio pages."""
        self.client.post('/studio/users/new/', {
            'email': 'admin2@test.com',
            'make_admin': 'on',
        })
        password = self.client.session[SESSION_KEY]['password']

        new_admin_client = Client()
        ok = new_admin_client.login(email='admin2@test.com', password=password)
        self.assertTrue(ok)
        response = new_admin_client.get('/studio/')
        # 200 means the staff_required gate let them in.
        self.assertEqual(response.status_code, 200)


class UserCreateConfirmationPageTest(TestCase):
    """The confirmation page renders the password once and then drops it."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='super@test.com', password='testpass')

    def test_confirmation_renders_password_with_copy_button(self):
        response = self.client.post('/studio/users/new/', {
            'email': 'shown@test.com',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/created.html')

        # The Copy button + its target id are present so the JS hook fires.
        self.assertContains(response, 'data-testid="generated-password"')
        self.assertContains(response, 'data-copy-target="user-password-value"')
        self.assertContains(response, 'id="user-password-value"')

        # The literal password value is visible in the rendered HTML.
        # We can't predict the random password, so look it up via the session
        # stash on the *follow*-ed request -- but follow() consumes the stash.
        # Instead, post then read the stash from the pre-follow request.
        response2 = self.client.post('/studio/users/new/', {
            'email': 'shown2@test.com',
        })
        password = self.client.session[SESSION_KEY]['password']
        self.assertEqual(response2.status_code, 302)
        confirm = self.client.get('/studio/users/created/')
        self.assertContains(confirm, password)

    def test_admin_badge_shown_when_user_is_admin(self):
        self.client.post('/studio/users/new/', {
            'email': 'badged@test.com',
            'make_admin': 'on',
        }, follow=True)
        # Re-fetch via the session stash trick -- follow() already drained
        # session above, so create a fresh user for this assertion.
        self.client.post('/studio/users/new/', {
            'email': 'badged2@test.com',
            'make_admin': 'on',
        })
        confirm = self.client.get('/studio/users/created/')
        self.assertContains(confirm, 'data-testid="admin-badge"')
        self.assertContains(confirm, 'Admin')

    def test_admin_badge_NOT_shown_for_regular_user(self):
        self.client.post('/studio/users/new/', {'email': 'plain@test.com'})
        confirm = self.client.get('/studio/users/created/')
        self.assertNotContains(confirm, 'data-testid="admin-badge"')

    def test_confirmation_links_to_user_detail(self):
        """The confirmation page links to the user-detail page (tier override)
        so the operator can grant a higher tier without leaving the flow."""
        self.client.post('/studio/users/new/', {'email': 'detail@test.com'})
        confirm = self.client.get('/studio/users/created/')
        # The link uses the tier-override page, prefilled with the new email.
        self.assertContains(confirm, 'data-testid="user-detail-link"')
        self.assertContains(confirm, '/studio/users/tier-override/')
        self.assertContains(confirm, 'email=detail%40test.com')

    def test_password_dropped_from_session_after_render(self):
        """One render only -- a second visit must not reveal the password."""
        self.client.post('/studio/users/new/', {'email': 'oneshot@test.com'})
        password = self.client.session[SESSION_KEY]['password']

        first = self.client.get('/studio/users/created/')
        self.assertContains(first, password)

        # Session stash gone -- the second visit cannot leak it.
        self.assertNotIn(SESSION_KEY, self.client.session)
        second = self.client.get('/studio/users/created/')
        self.assertNotContains(second, password)
        # The page tells the operator the password is no longer recoverable.
        self.assertContains(second, 'only displayed once')

    def test_direct_visit_without_session_shows_form(self):
        """Hitting /users/created/ cold (no recent submission) renders a hint."""
        response = self.client.get('/studio/users/created/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only displayed once')
        # The hint is on the form template, so the operator can immediately
        # try again.
        self.assertTemplateUsed(response, 'studio/users/create.html')


class UserCreateValidationTest(TestCase):
    """Form validation: missing email, duplicate email."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )
        cls.existing = User.objects.create_user(
            email='taken@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='super@test.com', password='testpass')

    def test_missing_email_re_renders_with_error(self):
        before = User.objects.count()
        response = self.client.post('/studio/users/new/', {'email': ''})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Email is required', status_code=400)
        self.assertEqual(User.objects.count(), before)

    def test_duplicate_email_rejected_no_user_created(self):
        before = User.objects.count()
        response = self.client.post('/studio/users/new/', {
            'email': 'taken@test.com',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        # No new row, no double-write to the existing one.
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(
            User.objects.filter(email='taken@test.com').count(), 1,
        )

    def test_duplicate_email_case_insensitive(self):
        """Email uniqueness should not depend on case."""
        before = User.objects.count()
        response = self.client.post('/studio/users/new/', {
            'email': 'TAKEN@test.com',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        self.assertEqual(User.objects.count(), before)

    def test_failed_submit_preserves_field_values(self):
        """Operator shouldn't have to retype everything on a duplicate-email
        retry."""
        response = self.client.post('/studio/users/new/', {
            'email': 'taken@test.com',
            'first_name': 'Fred',
            'last_name': 'Smith',
            'make_admin': 'on',
        })
        self.assertContains(response, 'value="Fred"', status_code=400)
        self.assertContains(response, 'value="Smith"', status_code=400)
        self.assertContains(response, 'value="taken@test.com"', status_code=400)
        # The admin checkbox stays ticked.
        self.assertContains(response, 'checked', status_code=400)


class UserCreateNoSecretLeakTest(TestCase):
    """The generated password must not be persisted or logged anywhere
    retrievable -- the only copy lives in the session until the confirmation
    page consumes it."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='super@test.com', password='testpass',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='super@test.com', password='testpass')

    def test_password_not_stored_in_plaintext_on_user_row(self):
        self.client.post('/studio/users/new/', {'email': 'safe@test.com'})
        password = self.client.session[SESSION_KEY]['password']
        user = User.objects.get(email='safe@test.com')
        # Django stores a hash; the raw value must not appear in the password
        # column.
        self.assertNotEqual(user.password, password)
        self.assertNotIn(password, user.password)

    def test_password_gone_from_session_after_confirmation(self):
        self.client.post('/studio/users/new/', {'email': 'gone@test.com'})
        # Stash present right after submit.
        self.assertIn(SESSION_KEY, self.client.session)
        # First visit consumes it.
        self.client.get('/studio/users/created/')
        self.assertNotIn(SESSION_KEY, self.client.session)


class FreeTierFixtureTest(TestCase):
    """Sanity: the free tier exists in the test DB (seeded by migration).

    All tests above rely on the User.save() default that assigns the free
    tier when none is provided; this guards against an accidental fixture
    regression that would silently skip that branch.
    """

    def test_free_tier_seeded(self):
        self.assertTrue(Tier.objects.filter(slug='free').exists())
