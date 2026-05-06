"""Tests for the member profile page (issue #439).

Covers ``/account/profile`` GET pre-fill / POST save / validation, the
"Profile" discovery card on ``/account/``, the mobile header Profile link,
and the round-trip with the contacts import + export API. The Studio
user-create form is also smoke-checked so the unrelated regression
called out in the spec is caught here.
"""

from django.test import TestCase

from accounts.models import Token, User


class ProfileViewGetTest(TestCase):
    """GET /account/profile renders the form pre-filled from the user row."""

    def test_get_pre_fills_form_with_current_values(self):
        user = User.objects.create_user(email="alice@example.com")
        user.first_name = "Alice"
        user.last_name = "Doe"
        user.save(update_fields=["first_name", "last_name"])
        self.client.force_login(user)

        response = self.client.get("/account/profile")

        self.assertEqual(response.status_code, 200)
        # Assert against the specific input element, not the whole body, so
        # the test fails for the right reason if the field renders blank.
        content = response.content.decode()
        first_idx = content.find('id="id_first_name"')
        self.assertNotEqual(first_idx, -1, "first_name input must render")
        first_tag = content[content.rfind("<", 0, first_idx):content.find(">", first_idx) + 1]
        self.assertIn('name="first_name"', first_tag)
        self.assertIn('value="Alice"', first_tag)

        last_idx = content.find('id="id_last_name"')
        self.assertNotEqual(last_idx, -1, "last_name input must render")
        last_tag = content[content.rfind("<", 0, last_idx):content.find(">", last_idx) + 1]
        self.assertIn('name="last_name"', last_tag)
        self.assertIn('value="Doe"', last_tag)

    def test_get_renders_empty_inputs_for_user_without_name(self):
        user = User.objects.create_user(email="empty@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/profile")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        first_idx = content.find('id="id_first_name"')
        first_tag = content[content.rfind("<", 0, first_idx):content.find(">", first_idx) + 1]
        self.assertIn('value=""', first_tag)
        last_idx = content.find('id="id_last_name"')
        last_tag = content[content.rfind("<", 0, last_idx):content.find(">", last_idx) + 1]
        self.assertIn('value=""', last_tag)


class ProfileViewPostTest(TestCase):
    """POST /account/profile updates first_name and last_name."""

    def test_post_updates_user_and_redirects(self):
        user = User.objects.create_user(email="post@example.com")
        self.client.force_login(user)

        response = self.client.post(
            "/account/profile",
            {"first_name": "Alice", "last_name": "Doe"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/account/profile")
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Doe")

    def test_post_with_empty_values_clears_existing_values(self):
        user = User.objects.create_user(email="clear@example.com")
        user.first_name = "Alice"
        user.last_name = "Doe"
        user.save(update_fields=["first_name", "last_name"])
        self.client.force_login(user)

        response = self.client.post(
            "/account/profile",
            {"first_name": "", "last_name": ""},
        )

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")

    def test_post_strips_surrounding_whitespace(self):
        # Behaviour: the spec says ``request.POST.get(...).strip()`` -- pin
        # this so a future refactor that drops the strip would break the
        # round-trip with the import API (which also strips).
        user = User.objects.create_user(email="strip@example.com")
        self.client.force_login(user)

        self.client.post(
            "/account/profile",
            {"first_name": "  Alice  ", "last_name": "  Doe  "},
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Doe")

    def test_post_too_long_first_name_renders_error_and_does_not_save(self):
        user = User.objects.create_user(email="long@example.com")
        user.first_name = "Alice"
        user.save(update_fields=["first_name"])
        self.client.force_login(user)

        long_name = "A" * 200
        response = self.client.post(
            "/account/profile",
            {"first_name": long_name, "last_name": "Doe"},
        )

        # Re-rendered, not redirected, with an inline error.
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Name is too long",
            status_code=400,
        )
        # The stored value must not have changed.
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "")

    def test_success_message_visible_after_save_redirect(self):
        user = User.objects.create_user(email="msg@example.com")
        self.client.force_login(user)

        response = self.client.post(
            "/account/profile",
            {"first_name": "Alice", "last_name": "Doe"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        # The Django messages framework drains in the base template; the
        # final page must render the success copy at least once.
        self.assertContains(response, "Your profile has been updated.")


class ProfileViewAccessTest(TestCase):
    """Anonymous access to /account/profile is denied (login redirect)."""

    def test_anonymous_get_redirects_to_login_with_next(self):
        response = self.client.get("/account/profile")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertIn("next=/account/profile", response.url)

    def test_anonymous_post_does_not_mutate_any_user(self):
        # Establish a user so the test can prove no row was touched.
        user = User.objects.create_user(email="alice@example.com")
        user.first_name = "Alice"
        user.last_name = "Doe"
        user.save(update_fields=["first_name", "last_name"])

        response = self.client.post(
            "/account/profile",
            {"first_name": "Mallory", "last_name": "Hacker"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Doe")


class AccountPageProfileSectionTest(TestCase):
    """The Profile section card on /account/ must link to /account/profile."""

    def test_account_page_shows_current_name_and_edit_link(self):
        user = User.objects.create_user(email="show@example.com")
        user.first_name = "Alice"
        user.last_name = "Doe"
        user.save(update_fields=["first_name", "last_name"])
        self.client.force_login(user)

        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)

        # Link to the profile editor must exist.
        self.assertContains(response, 'href="/account/profile"')
        # Current name is rendered inside the profile card.
        content = response.content.decode()
        section_idx = content.find('id="profile-current-name"')
        self.assertNotEqual(section_idx, -1, "profile-current-name element missing")
        tag_end = content.find(">", section_idx)
        close_idx = content.find("</p>", tag_end)
        rendered = content[tag_end + 1:close_idx].strip()
        self.assertEqual(rendered, "Alice Doe")

    def test_account_page_says_name_not_set_for_empty_user(self):
        user = User.objects.create_user(email="noname@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        self.assertContains(response, "Your name is not set yet")
        self.assertContains(response, 'href="/account/profile"')


class MobileHeaderProfileLinkTest(TestCase):
    """Mobile menu (authenticated user) gains a Profile link."""

    def test_authenticated_mobile_menu_has_profile_link(self):
        user = User.objects.create_user(email="mobile@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()
        # Scope the assertion to the new mobile-only Profile button so the
        # test isn't satisfied by some unrelated 'Profile' string appearing
        # in the page (e.g. the new Profile section card on /account/).
        idx = content.find('id="mobile-profile-link"')
        self.assertNotEqual(idx, -1, "mobile-profile-link element missing")
        tag_end = content.find(">", idx)
        tag_start = content.rfind("<", 0, idx)
        link_tag = content[tag_start:tag_end + 1]
        self.assertIn('href="/account/profile"', link_tag)

    def test_anonymous_mobile_menu_has_no_profile_link(self):
        # Use a public page that includes the header so the anonymous block
        # is rendered. The home page includes the public header.
        response = self.client.get("/")
        content = response.content.decode()
        self.assertNotIn('id="mobile-profile-link"', content)


class ContactsExportRoundTripTest(TestCase):
    """Saving a name in the profile must surface in the contacts API export.

    The export side already exists from issue #431. This test pins that the
    round-trip from the profile UI -> the API consumer keeps working.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin-export@example.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="export-roundtrip")

    def test_export_returns_name_after_profile_save(self):
        user = User.objects.create_user(email="member@example.com")
        self.client.force_login(user)

        # Save name through the new profile form.
        save_response = self.client.post(
            "/account/profile",
            {"first_name": "Alice", "last_name": "Doe"},
        )
        self.assertEqual(save_response.status_code, 302)

        # Drop the session and call the API as the admin token holder.
        self.client.logout()
        export_response = self.client.get(
            "/api/contacts/export",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(export_response.status_code, 200)

        body = export_response.json()
        member_row = next(
            (c for c in body["contacts"] if c["email"] == "member@example.com"),
            None,
        )
        self.assertIsNotNone(member_row, "exported contacts must include the member")
        self.assertEqual(member_row["first_name"], "Alice")
        self.assertEqual(member_row["last_name"], "Doe")


class ContactsImportRoundTripTest(TestCase):
    """Importing a name through the API must pre-fill the profile form.

    Pins the contract on the import side (issue #437) end-to-end with the
    new profile UI.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin-import@example.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="import-roundtrip")

    def test_imported_name_pre_fills_profile_form(self):
        bob = User.objects.create_user(email="bob@example.com")
        # Confirm starting state is empty so the import has something to set.
        self.assertEqual(bob.first_name, "")
        self.assertEqual(bob.last_name, "")

        import_response = self.client.post(
            "/api/contacts/import",
            data={
                "contacts": [
                    {
                        "email": "bob@example.com",
                        "first_name": "Bob",
                        "last_name": "Smith",
                    }
                ]
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(import_response.status_code, 200)

        # Log in as Bob and load the profile form.
        self.client.force_login(bob)
        response = self.client.get("/account/profile")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        first_idx = content.find('id="id_first_name"')
        first_tag = content[content.rfind("<", 0, first_idx):content.find(">", first_idx) + 1]
        self.assertIn('value="Bob"', first_tag)
        last_idx = content.find('id="id_last_name"')
        last_tag = content[content.rfind("<", 0, last_idx):content.find(">", last_idx) + 1]
        self.assertIn('value="Smith"', last_tag)


class StudioUserCreateRegressionTest(TestCase):
    """Smoke regression: the Studio user-create form still renders.

    The profile feature does not touch ``studio/views/users.py`` or its
    template, but the spec lists this as a regression assertion. One
    request gives us a load-bearing canary if a future refactor breaks
    the create page.
    """

    def test_studio_user_create_page_renders_with_name_inputs(self):
        admin = User.objects.create_user(
            email="admin-studio@example.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(admin)

        response = self.client.get("/studio/users/new/")
        self.assertEqual(response.status_code, 200)
        # The first/last name inputs the spec calls out must still be there.
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')
