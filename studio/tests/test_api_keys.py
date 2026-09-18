"""Studio management of ``community_base.api.APIKey`` (issues #1656, #1737).

Provenance. Investigation #1656 reported that ``/studio/api-keys/`` leaked
every member's key metadata to a superuser. The nine pinning tests that lived
here as ``test_api_keys_page_visibility_1656.py`` rendered the page for real
and split that reading into two separate facts:

1. The package view's queryset really was unfiltered -- ``APIKey.objects
   .select_related("user")`` with no owner or kind scoping, so
   ``response.context["api_keys"]`` contained a member-owned key the
   requesting superuser did not own.
2. None of it reached the HTML. ``community_base/api/api_keys.html`` puts its
   whole body in ``{% block content %}`` while the site shell defines only
   ``{% block studio_content %}``, so the block was silently dropped and the
   page was an empty Studio shell -- HTTP 200, right title, nothing else.

Fact 2 is the only reason fact 1 was not a live disclosure, which made the
obvious fix (make the page render) the exact moment the leak became real.
Issue #1737 therefore fixes both halves at once: the site takes ownership of
the views so the queryset can be scoped to ``kind="staff"``, and the page
renders through ``studio/base.html`` with a populated ``studio_content``.

These tests are the #1656 pinning tests inverted one-for-one against the
#1737 contract, plus coverage for the create/one-shot/revoke lifecycle the
page now owns. ``test_rendered_page_never_contains_the_plaintext_key`` is
kept verbatim: it asserted the right thing before and after.
"""

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import resolve, reverse

from studio.views.api_keys import SESSION_KEY as CREATE_RESULT_SESSION_KEY

User = get_user_model()

API_KEYS_URL = "/studio/api-keys/"
API_KEY_CREATE_URL = "/studio/api-keys/new/"
API_KEY_CREATED_URL = "/studio/api-keys/created/"

MEMBER_EMAIL = "member-1656@test.com"
MEMBER_KEY_NAME = "member laptop key"
MEMBER_KEY_SCOPES = ["courses.read", "users.read"]

STAFF_KEY_NAME = "settings sync script"
STAFF_KEY_SCOPES = ["settings.read"]


class StudioApiKeysSuperuserViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="super-1656@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.other_staff = User.objects.create_user(
            email="otherstaff-1737@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email=MEMBER_EMAIL,
            password="testpass",
        )
        cls.member_key, cls.member_plaintext = APIKey.create_for_user(
            user=cls.member,
            name=MEMBER_KEY_NAME,
            scopes=MEMBER_KEY_SCOPES,
            kind=APIKey.Kind.MEMBER,
        )
        cls.staff_key, cls.staff_plaintext = APIKey.create_for_user(
            user=cls.other_staff,
            name=STAFF_KEY_NAME,
            scopes=STAFF_KEY_SCOPES,
            kind=APIKey.Kind.STAFF,
        )

    def _get_as_superuser(self):
        client = Client()
        self.assertTrue(
            client.login(email="super-1656@test.com", password="testpass")
        )
        return client.get(API_KEYS_URL)

    def test_no_template_comment_syntax_leaks_into_any_rendered_page(self):
        """Multi-line ``{# #}`` is not a comment and renders as page content.

        Django's ``tag_re`` is ``({%.*?%}|{{.*?}}|{#.*?#})`` compiled without
        ``re.DOTALL``, so a ``{# ... #}`` spanning a newline never tokenizes
        and the raw text reaches the browser. Inside a ``<table>`` but outside
        a cell the HTML parser foster-parents it above the table and repeats
        it per row, so developer commentary becomes the largest thing on the
        page. Use ``{% comment %}`` for anything multi-line. This is a known
        recurring trap in this repo, so it is pinned rather than trusted.
        """
        client = Client()
        self.assertTrue(
            client.login(email="super-1656@test.com", password="testpass")
        )
        one_shot, plaintext = APIKey.create_for_user(
            user=self.superuser,
            name="one shot key",
            scopes=["settings.read"],
            kind=APIKey.Kind.STAFF,
        )
        session = client.session
        session[CREATE_RESULT_SESSION_KEY] = {
            "key": plaintext,
            "pk": str(one_shot.pk),
        }
        session.save()

        for url in (
            API_KEYS_URL,
            reverse("studio_api_key_create"),
            reverse("studio_api_key_created"),
        ):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertNotContains(response, "{#")
                self.assertNotContains(response, "#}")
                self.assertNotContains(response, "{%")
                self.assertNotContains(response, "endcomment")

    # --- Inverted fact 1: the queryset is scoped to staff-kind keys ----- #
    def test_context_queryset_is_scoped_to_staff_kind_keys(self):
        response = self._get_as_superuser()

        self.assertContains(response, 'data-testid="api-keys-list"')
        listed = list(response.context["api_keys"])
        self.assertIn(self.staff_key, listed)
        self.assertNotIn(self.member_key, listed)
        self.assertEqual({key.kind for key in listed}, {APIKey.Kind.STAFF})

    # --- Inverted fact 2: staff metadata renders, member metadata never - #
    def test_rendered_page_contains_staff_key_metadata_only(self):
        response = self._get_as_superuser()

        self.assertContains(response, STAFF_KEY_NAME)
        self.assertContains(response, self.other_staff.email)
        # Issue #1737: the row shows an identifying stub (kind prefix + four
        # characters), never the full 24-character lookup prefix. The stub is
        # enough to tell two keys apart; the rest is live key material.
        self.assertContains(response, self.staff_key.masked_prefix[:13])
        self.assertNotContains(response, self.staff_key.masked_prefix[:14])
        self.assertContains(response, "settings.read")

        self.assertNotContains(response, MEMBER_EMAIL)
        self.assertNotContains(response, MEMBER_KEY_NAME)
        self.assertNotContains(response, self.member_key.masked_prefix)
        self.assertNotContains(response, "courses.read")
        self.assertNotContains(response, "users.read")

    def test_rendered_page_contains_key_management_controls(self):
        """The page that lost its block to a name mismatch now renders one."""
        response = self._get_as_superuser()

        self.assertContains(response, 'data-testid="api-key-create-link"')
        self.assertContains(response, 'data-testid="api-key-revoke"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(
            response, f'action="{API_KEYS_URL}{self.staff_key.id}/revoke/"'
        )

    def test_page_renders_inside_the_studio_shell_with_a_header(self):
        """Pin the fix: site template, right block name, populated shell."""
        response = self._get_as_superuser()

        used = [template.name for template in response.templates]
        self.assertIn("studio/api_keys/list.html", used)
        self.assertIn("studio/base.html", used)
        self.assertNotIn("community_base/api/api_keys.html", used)
        self.assertContains(response, 'data-testid="studio-header"')
        self.assertContains(response, "API keys")

    def test_rendered_page_never_contains_the_plaintext_key(self):
        response = self._get_as_superuser()

        self.assertNotContains(response, self.member_plaintext)

    def test_rendered_page_never_contains_a_staff_plaintext_key(self):
        response = self._get_as_superuser()

        self.assertNotContains(response, self.staff_plaintext)

    # --- The POST route only reaches operator credentials --------------- #
    def test_superuser_can_revoke_a_staff_key_they_do_not_own(self):
        key, _ = APIKey.create_for_user(
            user=self.other_staff,
            name="second staff key",
            scopes=["settings.read"],
            kind=APIKey.Kind.STAFF,
        )
        client = Client()
        client.login(email="super-1656@test.com", password="testpass")

        response = client.post(f"{API_KEYS_URL}{key.id}/revoke/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], API_KEYS_URL)
        key.refresh_from_db()
        self.assertIsNotNone(key.revoked_at)

    def test_superuser_cannot_revoke_a_member_kind_key(self):
        key, _ = APIKey.create_for_user(
            user=self.member,
            name="second member key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        client = Client()
        client.login(email="super-1656@test.com", password="testpass")

        response = client.post(f"{API_KEYS_URL}{key.id}/revoke/")

        self.assertEqual(response.status_code, 404)
        key.refresh_from_db()
        self.assertIsNone(key.revoked_at)

    def test_revoked_row_shows_revoked_status_and_no_revoke_control(self):
        client = Client()
        client.login(email="super-1656@test.com", password="testpass")
        client.post(f"{API_KEYS_URL}{self.staff_key.id}/revoke/")

        response = client.get(API_KEYS_URL, follow=True)

        self.assertContains(response, "API key revoked.")
        self.assertContains(response, "Revoked")
        self.assertNotContains(response, 'data-testid="api-key-revoke"')
        self.assertNotContains(
            response, f'action="{API_KEYS_URL}{self.staff_key.id}/revoke/"'
        )

    def test_empty_state_renders_when_no_staff_keys_exist(self):
        APIKey.objects.filter(kind=APIKey.Kind.STAFF).delete()

        response = self._get_as_superuser()

        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        self.assertContains(response, "No API keys yet.")
        self.assertContains(response, "Create key")
        # A member-kind key still in the table must not resurrect the list.
        self.assertTrue(APIKey.objects.filter(kind=APIKey.Kind.MEMBER).exists())
        self.assertNotContains(response, MEMBER_KEY_NAME)


class StudioApiKeysAccessControlTest(TestCase):
    """Only a superuser reaches the page at all."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_only = User.objects.create_user(
            email="staffonly-1656@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="plain-1656@test.com",
            password="testpass",
        )
        APIKey.create_for_user(
            user=cls.member,
            name=MEMBER_KEY_NAME,
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        cls.staff_key, _ = APIKey.create_for_user(
            user=cls.staff_only,
            name=STAFF_KEY_NAME,
            scopes=STAFF_KEY_SCOPES,
            kind=APIKey.Kind.STAFF,
        )

    def test_anonymous_is_redirected_to_login(self):
        response = Client().get(API_KEYS_URL)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_anonymous_is_redirected_from_every_api_key_route(self):
        client = Client()
        for url in (API_KEYS_URL, API_KEY_CREATE_URL, API_KEY_CREATED_URL):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response["Location"])

        response = client.post(f"{API_KEYS_URL}{self.staff_key.id}/revoke/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.staff_key.refresh_from_db()
        self.assertIsNone(self.staff_key.revoked_at)

    def test_staff_non_superuser_does_not_get_the_page(self):
        client = Client()
        client.login(email="staffonly-1656@test.com", password="testpass")

        response = client.get(API_KEYS_URL)

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, MEMBER_KEY_NAME, status_code=403)

    def test_staff_non_superuser_sees_no_key_metadata_at_all(self):
        client = Client()
        client.login(email="staffonly-1656@test.com", password="testpass")

        response = client.get(API_KEYS_URL)

        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertNotIn(STAFF_KEY_NAME, body)
        self.assertNotIn(self.staff_only.email, body)
        self.assertNotIn(self.staff_key.masked_prefix, body)
        self.assertNotIn("settings.read", body)

    def test_staff_non_superuser_cannot_revoke_a_members_key(self):
        key = APIKey.objects.get(name=MEMBER_KEY_NAME)
        client = Client()
        client.login(email="staffonly-1656@test.com", password="testpass")

        response = client.post(
            f"{API_KEYS_URL}{key.id}/revoke/", {"confirmation": "revoke"}
        )

        self.assertNotEqual(response.status_code, 302)
        key.refresh_from_db()
        self.assertIsNone(key.revoked_at)

    def test_staff_non_superuser_cannot_revoke_a_staff_key(self):
        client = Client()
        client.login(email="staffonly-1656@test.com", password="testpass")

        response = client.post(f"{API_KEYS_URL}{self.staff_key.id}/revoke/")

        self.assertNotEqual(response.status_code, 302)
        self.staff_key.refresh_from_db()
        self.assertIsNone(self.staff_key.revoked_at)

    def test_staff_non_superuser_does_not_see_the_sidebar_link(self):
        client = Client()
        client.login(email="staffonly-1656@test.com", password="testpass")

        response = client.get("/studio/")

        self.assertNotContains(response, 'data-testid="api-keys-nav-link"')


class StudioApiKeyCreateTest(TestCase):
    """Minting is constrained: owner is the operator, kind is always staff."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="creator-1737@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.member = User.objects.create_user(
            email="victim-1737@test.com",
            password="testpass",
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_valid_post_creates_a_staff_key_owned_by_the_operator(self):
        response = self.client.post(
            API_KEY_CREATE_URL,
            {"name": STAFF_KEY_NAME, "scopes": "settings.read"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], API_KEY_CREATED_URL)
        key = APIKey.objects.get(name=STAFF_KEY_NAME)
        self.assertEqual(key.user, self.superuser)
        self.assertEqual(key.kind, APIKey.Kind.STAFF)
        self.assertEqual(key.scopes, ["settings.read"])

    def test_form_has_no_recipient_or_kind_field(self):
        response = self.client.get(API_KEY_CREATE_URL)

        self.assertTemplateUsed(response, "studio/api_keys/create.html")
        form = response.context["form"]
        self.assertEqual(sorted(form.fields), ["name", "scopes"])
        self.assertNotContains(response, 'name="user"')
        self.assertNotContains(response, 'name="kind"')

    def test_posted_user_and_kind_are_ignored(self):
        response = self.client.post(
            API_KEY_CREATE_URL,
            {
                "name": "smuggled",
                "scopes": "settings.read",
                "user": str(self.member.pk),
                "kind": APIKey.Kind.MEMBER,
            },
        )

        self.assertEqual(response.status_code, 302)
        key = APIKey.objects.get(name="smuggled")
        self.assertEqual(key.user, self.superuser)
        self.assertEqual(key.kind, APIKey.Kind.STAFF)

    def test_create_form_help_text_lists_the_mounted_route_scopes(self):
        response = self.client.get(API_KEY_CREATE_URL)

        help_text = response.context["form"].fields["scopes"].help_text
        self.assertIn("settings.read", help_text)
        self.assertIn("settings.write", help_text)
        self.assertIn("content_sync.read", help_text)
        self.assertContains(response, "settings.read")

    def test_invalid_scope_re_renders_the_form_and_keeps_typed_values(self):
        response = self.client.post(
            API_KEY_CREATE_URL,
            {"name": "import script", "scopes": "Bad Scope!"},
        )

        self.assertTemplateUsed(response, "studio/api_keys/create.html")
        self.assertFalse(APIKey.objects.exists())
        self.assertContains(response, "Invalid scope: Bad Scope!")
        self.assertContains(response, 'data-testid="form-error-scopes"')
        self.assertContains(response, 'value="import script"')

    def test_blank_name_re_renders_the_form_with_a_field_error(self):
        response = self.client.post(
            API_KEY_CREATE_URL,
            {"name": "", "scopes": "settings.read"},
        )

        self.assertTemplateUsed(response, "studio/api_keys/create.html")
        self.assertFalse(APIKey.objects.exists())
        self.assertContains(response, 'data-testid="form-error-name"')
        self.assertIn("name", response.context["form"].errors)

    def test_blank_scopes_re_renders_the_form_with_a_field_error(self):
        response = self.client.post(
            API_KEY_CREATE_URL,
            {"name": "no scopes", "scopes": ""},
        )

        self.assertTemplateUsed(response, "studio/api_keys/create.html")
        self.assertFalse(APIKey.objects.exists())
        self.assertIn("scopes", response.context["form"].errors)

    def test_superuser_who_is_not_staff_gets_a_form_error_not_a_500(self):
        rootless = User.objects.create_user(
            email="rootless-1737@test.com",
            password="testpass",
            is_staff=False,
            is_superuser=True,
        )
        self.client.force_login(rootless)

        response = self.client.post(
            API_KEY_CREATE_URL,
            {"name": "doomed", "scopes": "settings.read"},
        )

        self.assertTemplateUsed(response, "studio/api_keys/create.html")
        self.assertFalse(APIKey.objects.exists())
        self.assertContains(response, "Staff API keys require a staff user.")
        self.assertContains(response, 'data-testid="form-error-non-field"')


class StudioApiKeyCreatedOneShotTest(TestCase):
    """The plaintext appears on exactly one render and leaves no residue."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="oneshot-1737@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_plaintext_is_shown_once_then_the_page_redirects(self):
        self.client.post(
            API_KEY_CREATE_URL,
            {"name": STAFF_KEY_NAME, "scopes": "settings.read"},
        )

        first = self.client.get(API_KEY_CREATED_URL)
        self.assertTemplateUsed(first, "studio/api_keys/created.html")
        plaintext = first.context["plaintext_key"]
        self.assertTrue(plaintext.startswith("cb_staff_"))
        self.assertContains(first, plaintext)

        second = self.client.get(API_KEY_CREATED_URL)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second["Location"], API_KEYS_URL)
        self.assertNotIn("studio_api_key_create_result", self.client.session)

        listing = self.client.get(API_KEYS_URL)
        self.assertNotContains(listing, plaintext)

    def test_created_page_without_a_stash_redirects_to_the_list(self):
        response = self.client.get(API_KEY_CREATED_URL)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], API_KEYS_URL)


class StudioApiKeyRouteNameTest(TestCase):
    """The package-era route names still reverse, now to the site views."""

    def test_package_aliases_reverse_to_the_site_urls(self):
        self.assertEqual(reverse("community_base_api_keys"), API_KEYS_URL)
        self.assertEqual(
            reverse("community_base_api_key_revoke", args=["key_abc"]),
            f"{API_KEYS_URL}key_abc/revoke/",
        )
        self.assertEqual(
            resolve(API_KEYS_URL).func.__module__, "studio.views.api_keys"
        )


class StudioApiKeyAuthenticatesVersionedApiTest(TestCase):
    """A key minted through the page is a working /api/v1/ credential."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="apiuser-1737@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

    def _mint(self, name, scopes):
        self.client.force_login(self.superuser)
        self.client.post(API_KEY_CREATE_URL, {"name": name, "scopes": scopes})
        return self.client.get(API_KEY_CREATED_URL).context["plaintext_key"]

    def test_minted_key_authenticates_only_within_its_scopes(self):
        allowed = self._mint("settings reader", "settings.read")
        denied = self._mint("content reader", "content_sync.read")

        anonymous = Client().get("/api/v1/settings")
        self.assertEqual(anonymous.status_code, 401)

        ok = Client().get(
            "/api/v1/settings", HTTP_AUTHORIZATION=f"Bearer {allowed}"
        )
        self.assertIn("settings", ok.json())
        self.assertIn("pagination", ok.json())

        forbidden = Client().get(
            "/api/v1/settings", HTTP_AUTHORIZATION=f"Bearer {denied}"
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_revoking_through_studio_cuts_real_api_access(self):
        plaintext = self._mint("doomed script", "settings.read")
        key = APIKey.objects.get(name="doomed script")

        before = Client().get(
            "/api/v1/settings", HTTP_AUTHORIZATION=f"Bearer {plaintext}"
        )
        self.assertIn("settings", before.json())

        self.client.force_login(self.superuser)
        self.client.post(f"{API_KEYS_URL}{key.id}/revoke/")

        after = Client().get(
            "/api/v1/settings", HTTP_AUTHORIZATION=f"Bearer {plaintext}"
        )
        self.assertEqual(after.status_code, 401)
