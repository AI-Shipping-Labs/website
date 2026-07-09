"""Tests for the Studio API token management UI (issue #431)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
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
        cls.plaintext_key = cls.token.key

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
        body_text = response.content.decode()
        revoke_url = f"/studio/api-tokens/{self.token.pk}/revoke/"
        rotate_url = f"/studio/api-tokens/{self.token.pk}/rotate/"
        self.assertIn(revoke_url, body_text)
        self.assertIn(rotate_url, body_text)
        self.assertNotIn(
            self.plaintext_key, body_text,
            "Full plaintext key must not appear in page source or action URLs.",
        )

    def test_revoke_button_uses_destructive_palette(self):
        """Regression for #743: the kind passed to ``studio_action_class``
        must resolve to the destructive variant, not silently fall through
        to the neutral secondary grey. ``text-red-400`` is the
        distinguishing class of the destructive variant — see
        ``ACTION_KIND_CLASSES`` in ``studio/templatetags/studio_filters.py``.
        """
        response = self.client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Locate the Revoke button by its data-testid and slice its
        # opening tag so the assertion can't match red classes from
        # other parts of the page.
        marker = 'data-testid="api-token-revoke"'
        self.assertIn(marker, body)
        idx = body.index(marker)
        tag_end = body.index(">", idx)
        button_open_tag = body[idx:tag_end]
        self.assertIn(
            "text-red-400", button_open_tag,
            "Revoke button must render with the destructive-red palette "
            "(see issue #743).",
        )

    def test_empty_state_when_no_tokens_exist(self):
        Token.objects.all().delete()
        response = self.client.get("/studio/api-tokens/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No API tokens yet")
        # Canonical fresh-zero card from #756 partial.
        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        self.assertContains(response, 'data-empty-state="api-tokens-empty"')

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
        plaintext_key = self.client.session[SESSION_KEY]["key"]

        token = Token.objects.get(name="import-script")
        # Token is owned by the signed-in superuser.
        self.assertEqual(token.user, self.superuser)
        self.assertIsNone(token.key)
        self.assertNotEqual(token.pk, plaintext_key)
        self.assertNotEqual(token.key_hash, plaintext_key)
        self.assertNotEqual(token.lookup_prefix, plaintext_key)
        self.assertEqual(
            token.lookup_prefix,
            plaintext_key[:Token.LOOKUP_PREFIX_LENGTH],
        )
        self.assertTrue(check_password(plaintext_key, token.key_hash))

        followed = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(followed.status_code, 200)
        # The plaintext key appears exactly once on the page.
        self.assertContains(followed, plaintext_key, count=1)
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
        self.plaintext_key = self.token.key

    def test_revoke_deletes_token(self):
        response = self.client.post(
            f"/studio/api-tokens/{self.token.pk}/revoke/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/studio/api-tokens/")
        self.assertEqual(
            Token.objects.filter(pk=self.token.pk).count(), 0,
        )

    def test_revoke_rejects_get(self):
        response = self.client.get(
            f"/studio/api-tokens/{self.token.pk}/revoke/",
        )
        # require_POST returns 405 for GET.
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            Token.objects.filter(pk=self.token.pk).exists(),
        )

    def test_revoked_token_no_longer_authenticates(self):
        # Revoke via the Studio endpoint.
        self.client.post(f"/studio/api-tokens/{self.token.pk}/revoke/")

        # API request with the (now deleted) token must 401.
        # Use a fresh client with no Studio session.
        api_client = Client()
        response = api_client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {self.plaintext_key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})


class ApiTokenRotateTest(TestCase):
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
            user=self.superuser, name="to-rotate",
        )
        self.old_key = self.token.key

    def test_rotate_replaces_credential_and_shows_new_key_once(self):
        token_id = self.token.pk
        old_hash = self.token.key_hash
        response = self.client.post(f"/studio/api-tokens/{token_id}/rotate/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/studio/api-tokens/created/")
        new_key = self.client.session[SESSION_KEY]["key"]
        self.assertNotEqual(new_key, self.old_key)

        self.token.refresh_from_db()
        self.assertEqual(self.token.pk, token_id)
        self.assertNotEqual(self.token.key_hash, old_hash)
        self.assertTrue(check_password(new_key, self.token.key_hash))
        self.assertEqual(
            self.token.lookup_prefix,
            new_key[:Token.LOOKUP_PREFIX_LENGTH],
        )

        first = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "API token rotated")
        self.assertContains(first, "old token")
        self.assertContains(first, new_key, count=1)
        self.assertNotContains(first, self.old_key)

        second = self.client.get("/studio/api-tokens/created/")
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second["Location"], "/studio/api-tokens/")

        api_client = Client()
        old_response = api_client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {self.old_key}",
        )
        self.assertEqual(old_response.status_code, 401)
        self.assertEqual(old_response.json(), {"error": "Invalid token"})

        new_response = api_client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {new_key}",
        )
        self.assertEqual(new_response.status_code, 200)


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
