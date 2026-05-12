"""Tests for the Studio API token management UI (issue #431)."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.models import Token
from studio.views.api_tokens import RESERVED_SYSTEM_TOKEN_NAMES, SESSION_KEY

User = get_user_model()


class ApiTokenAccessControlTest(TestCase):
    """Only superusers can reach the token management views."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.staff_only = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com",
            password="testpass",
        )

    def test_anonymous_user_redirected_to_login(self):
        client = Client()
        response = client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/accounts/login/?next=/studio/api-tokens/",
        )

    def test_staff_non_superuser_gets_403(self):
        client = Client()
        client.login(email="staff@test.com", password="testpass")
        response = client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 403)

    def test_member_gets_403(self):
        client = Client()
        client.login(email="member@test.com", password="testpass")
        response = client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 403)


class ApiTokenListViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(
            user=cls.superuser, name="script-import",
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email="super@test.com", password="testpass")

    def test_superuser_sees_token_list(self):
        response = self.client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 200)
        # The token's name is rendered in a row.
        self.assertContains(response, "script-import")
        self.assertContains(response, "super@test.com")
        # The masked prefix is rendered as the visible key column.
        self.assertContains(
            response,
            f'data-testid="api-token-prefix">\n          {self.token.key_prefix}',
        )
        # The full plaintext key is not rendered as visible text. (The
        # revoke form's action URL legitimately contains the key as a
        # path parameter; that's not a "display" of the key.)
        # Verify the key does NOT appear inside any visible cell:
        body_text = response.content.decode()
        # Strip out the form action URL which contains the key as a path
        # segment, then assert the key isn't present anywhere else.
        action_url = f"/studio/api-tokens/{self.token.key}/revoke/"
        self.assertIn(action_url, body_text)
        body_without_action = body_text.replace(action_url, "")
        self.assertNotIn(
            self.token.key, body_without_action,
            "Full plaintext key must only appear in the revoke form action URL.",
        )

    def test_empty_state_when_no_tokens_exist(self):
        Token.objects.all().delete()
        response = self.client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No API tokens yet")

    def test_reserved_system_tokens_are_not_listed_as_operator_tokens(self):
        for name in RESERVED_SYSTEM_TOKEN_NAMES:
            Token.objects.create(user=self.superuser, name=name)

        response = self.client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "script-import")
        for name in RESERVED_SYSTEM_TOKEN_NAMES:
            self.assertNotContains(response, name)

    def test_empty_state_when_only_reserved_system_tokens_exist(self):
        Token.objects.all().delete()
        Token.objects.create(user=self.superuser, name="studio-plan-editor")

        response = self.client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No API tokens yet")
        self.assertNotContains(response, "studio-plan-editor")


class ApiTokenCreateFormTest(TestCase):
    """Create form binds the new token to the signed-in admin."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email="super@test.com", password="testpass")

    def test_create_form_has_no_user_field_and_shows_admin_email(self):
        response = self.client.get("/studio/api-tokens/new/")
        self.assertEqual(response.status_code, 200)

        form = response.context["form"]
        self.assertNotIn("user", form.fields)
        # The signed-in admin's email is rendered so the operator can see
        # whose token they are about to mint.
        self.assertContains(response, 'data-testid="token-owner-note"')
        self.assertContains(response, "super@test.com")
        # The user dropdown element must be gone entirely.
        self.assertNotContains(response, 'data-testid="token-user-select"')

    def test_create_form_rejects_reserved_system_name(self):
        response = self.client.post(
            "/studio/api-tokens/new/",
            {"name": "studio-plan-editor"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "name",
            "That token name is reserved for system-managed tokens.",
        )
        self.assertFalse(Token.objects.filter(name="studio-plan-editor").exists())

    def test_create_token_redirects_to_one_shot_view(self):
        response = self.client.post(
            "/studio/api-tokens/new/",
            {"name": "import-script"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/studio/api-tokens/created/")

        token = Token.objects.get(name="import-script")
        # Token is owned by the signed-in superuser.
        self.assertEqual(token.user, self.superuser)
        followed = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(followed.status_code, 200)
        # The plaintext key appears exactly once on the page.
        self.assertContains(followed, token.key, count=1)
        # The owner email shown on the created page is the current admin.
        self.assertContains(followed, "super@test.com")

    def test_create_token_with_empty_name_succeeds(self):
        response = self.client.post(
            "/studio/api-tokens/new/",
            {"name": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/studio/api-tokens/created/")
        token = Token.objects.get(user=self.superuser)
        self.assertEqual(token.name, "")


class ApiTokenOneShotViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email="super@test.com", password="testpass")

    def test_one_shot_view_clears_session_stash(self):
        # Mint a token via the form so the session stash is populated.
        self.client.post(
            "/studio/api-tokens/new/",
            {"name": "one-shot"},
        )
        self.assertIn(SESSION_KEY, self.client.session)

        first = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(first.status_code, 200)
        # Session stash drained.
        self.assertNotIn(SESSION_KEY, self.client.session)

        # Second visit redirects to the list -- the one-shot is gone.
        second = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second["Location"], "/studio/api-tokens/")

    def test_direct_visit_without_session_redirects_to_list(self):
        response = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/studio/api-tokens/")


class ApiTokenRevokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email="super@test.com", password="testpass")
        self.token = Token.objects.create(
            user=self.superuser, name="to-revoke",
        )

    def test_revoke_deletes_token(self):
        response = self.client.post(
            f"/studio/api-tokens/{self.token.key}/revoke/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/studio/api-tokens/")
        self.assertEqual(
            Token.objects.filter(key=self.token.key).count(), 0,
        )

    def test_revoke_rejects_get(self):
        response = self.client.get(
            f"/studio/api-tokens/{self.token.key}/revoke/",
        )
        # require_POST returns 405 for GET.
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            Token.objects.filter(key=self.token.key).exists(),
        )

    def test_revoked_token_no_longer_authenticates(self):
        revoked_key = self.token.key
        # Revoke via the Studio endpoint.
        self.client.post(f"/studio/api-tokens/{revoked_key}/revoke/")

        # API request with the (now deleted) token must 401.
        # Use a fresh client with no Studio session.
        api_client = Client()
        response = api_client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {revoked_key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})


class ApiTokenSidebarLinkTest(TestCase):
    """The 'API tokens' sidebar link is visible only to superusers."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.staff_only = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )

    def test_link_visible_to_superuser(self):
        client = Client()
        client.login(email="super@test.com", password="testpass")
        response = client.get("/studio/")
        self.assertEqual(response.status_code, 200)
        # Hits the conditional sidebar entry, gated on is_superuser.
        self.assertContains(response, 'data-testid="api-tokens-nav-link"')

    def test_link_hidden_from_staff_only(self):
        client = Client()
        client.login(email="staff@test.com", password="testpass")
        response = client.get("/studio/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="api-tokens-nav-link"')
