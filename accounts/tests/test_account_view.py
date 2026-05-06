"""Tests for the consolidated /account/ profile name form (issue #447).

Issue #447 folded the standalone ``/account/profile`` page back into
``/account/``. The legacy URL keeps responding (``301`` on GET / HEAD,
``POST`` writes the new name and redirects to ``/account/#profile``) so
saved bookmarks and the mobile header link keep working.

This file replaces ``accounts/tests/test_profile_view.py``:

- GET-renders-form assertions now target ``/account/`` instead of
  ``/account/profile``.
- POST persistence / validation target ``POST /account/profile`` and
  assert on the new redirect target ``/account/#profile``.
- ``MobileHeaderProfileLinkTest`` pins the new ``/account/#profile``
  ``href`` (anchor scroll target on the consolidated page).
- ``StudioUserCreateRegressionTest`` is preserved as a smoke check on
  the unrelated user-create form.
"""

from django.test import TestCase

from accounts.models import Token, User


class AccountPageProfileFormTest(TestCase):
    """GET /account/ renders the inline Profile name form."""

    def test_get_pre_fills_form_with_current_values(self):
        user = User.objects.create_user(email="alice@example.com")
        user.first_name = "Alice"
        user.last_name = "Doe"
        user.save(update_fields=["first_name", "last_name"])
        self.client.force_login(user)

        response = self.client.get("/account/")

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

        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        first_idx = content.find('id="id_first_name"')
        first_tag = content[content.rfind("<", 0, first_idx):content.find(">", first_idx) + 1]
        self.assertIn('value=""', first_tag)
        last_idx = content.find('id="id_last_name"')
        last_tag = content[content.rfind("<", 0, last_idx):content.find(">", last_idx) + 1]
        self.assertIn('value=""', last_tag)

    def test_get_renders_save_button_and_form_action(self):
        # Pin the Save button and the form ``action`` so re-arranging the
        # page cannot accidentally drop the inline form.
        user = User.objects.create_user(email="form@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()

        section_idx = content.find('id="profile-section"')
        self.assertNotEqual(section_idx, -1, "profile-section card must exist")

        # The Save button must render after the section opens but before
        # the next card starts (next ``<!-- `` comment marker).
        save_idx = content.find('id="profile-save-btn"', section_idx)
        self.assertNotEqual(save_idx, -1, "profile-save-btn must render")
        next_card_idx = content.find("<!-- ", section_idx + 1)
        self.assertNotEqual(next_card_idx, -1, "another card must follow Profile")
        self.assertLess(
            save_idx, next_card_idx,
            "Save button must live inside the profile card",
        )

        # The form ``action`` points at the legacy ``/account/profile``.
        action_idx = content.find('action="/account/profile"', section_idx)
        self.assertNotEqual(
            action_idx, -1,
            "Profile form must POST to /account/profile",
        )
        self.assertLess(
            action_idx, save_idx,
            "Form action must come before the Save button it wraps",
        )


class AccountPageProfileOrderTest(TestCase):
    """The Profile card sits at the top of the cards stack on /account/."""

    def test_profile_card_appears_before_other_cards(self):
        user = User.objects.create_user(email="order@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()

        profile_idx = content.find('id="profile-section"')
        self.assertNotEqual(profile_idx, -1, "profile-section must render")

        # Cards that must appear AFTER the Profile card on the page. Use
        # ``id=`` attribute markers (or a unique HTML comment for the
        # Membership block, which has no id) so the assertion is not
        # tripped by stray occurrences of the word elsewhere on the page.
        for marker in (
            "<!-- Membership Section -->",
            'id="email-preferences-section"',
            'id="display-preferences-section"',
            'id="change-password-section"',
            'id="account-info-section"',
        ):
            marker_idx = content.find(marker)
            self.assertNotEqual(
                marker_idx, -1,
                f"{marker!r} must render on /account/",
            )
            self.assertLess(
                profile_idx, marker_idx,
                f"Profile card must appear above {marker!r}",
            )


class AccountProfilePostTest(TestCase):
    """POST /account/profile updates first_name and last_name (issue #447)."""

    def test_post_updates_user_and_redirects_to_anchored_account_page(self):
        user = User.objects.create_user(email="post@example.com")
        self.client.force_login(user)

        response = self.client.post(
            "/account/profile",
            {"first_name": "Alice", "last_name": "Doe"},
        )

        self.assertEqual(response.status_code, 302)
        # Redirect target now points at /account/#profile so the user's
        # browser scrolls back to the form they just submitted.
        self.assertEqual(response.url, "/account/#profile")
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
        self.assertEqual(response.url, "/account/#profile")
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

    def test_post_too_long_first_name_renders_inline_error_on_account_page(self):
        # Overflow re-renders /account/ with a 400 and an inline error in
        # the Profile card. The user keeps their typed (rejected) input.
        user = User.objects.create_user(email="long@example.com")
        user.first_name = "Alice"
        user.save(update_fields=["first_name"])
        self.client.force_login(user)

        long_name = "A" * 200
        response = self.client.post(
            "/account/profile",
            {"first_name": long_name, "last_name": "Doe"},
        )

        self.assertEqual(response.status_code, 400)
        # Re-renders the account page (full template), not the deleted
        # ``accounts/profile.html``.
        self.assertTemplateUsed(response, "accounts/account.html")

        content = response.content.decode()
        # The error block carries a stable test selector.
        err_idx = content.find('data-testid="profile-form-error"')
        self.assertNotEqual(err_idx, -1, "profile-form-error must render")
        # And the copy is the spec-defined string.
        self.assertContains(
            response,
            "Name is too long",
            status_code=400,
        )

        # Rejected input is preserved in the form so the user can fix it.
        first_idx = content.find('id="id_first_name"')
        first_tag = content[content.rfind("<", 0, first_idx):content.find(">", first_idx) + 1]
        self.assertIn(f'value="{long_name}"', first_tag)
        last_idx = content.find('id="id_last_name"')
        last_tag = content[content.rfind("<", 0, last_idx):content.find(">", last_idx) + 1]
        self.assertIn('value="Doe"', last_tag)

        # The stored value must not have changed.
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "")

    def test_post_too_long_last_name_also_rejected(self):
        # Symmetry test -- ``last_name`` overflow has the same contract.
        user = User.objects.create_user(email="long-last@example.com")
        self.client.force_login(user)

        long_name = "Z" * 200
        response = self.client.post(
            "/account/profile",
            {"first_name": "Alice", "last_name": long_name},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Name is too long", status_code=400)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
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


class AccountProfileGetRedirectTest(TestCase):
    """GET / HEAD /account/profile -> 301 to /account/ (issue #447)."""

    def test_get_redirects_permanently_to_account(self):
        # Authenticated GET still 301s -- the form lives on /account/ now.
        user = User.objects.create_user(email="get@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/profile")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/account/")

    def test_head_redirects_permanently_to_account(self):
        user = User.objects.create_user(email="head@example.com")
        self.client.force_login(user)

        response = self.client.head("/account/profile")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/account/")

    def test_anonymous_get_still_redirects(self):
        # The redirect is unconditional; auth gating only applies to POST.
        # An anonymous bookmark visit lands on /account/ which then runs
        # its own login redirect -- but the FIRST hop is always 301.
        response = self.client.get("/account/profile")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/account/")


class AccountProfileAccessTest(TestCase):
    """Anonymous POST is bounced to login without mutating any user."""

    def test_anonymous_post_redirects_to_login_with_next(self):
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
        # Django/allauth percent-encodes the ``next`` param. Compare the
        # decoded form so the assertion is not coupled to the encoder.
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(response.url).query)
        self.assertEqual(qs.get("next"), ["/account/profile"])

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Doe")


class MobileHeaderProfileLinkTest(TestCase):
    """Mobile menu Profile link points at the anchored /account/#profile."""

    def test_authenticated_mobile_menu_has_anchored_profile_link(self):
        user = User.objects.create_user(email="mobile@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()
        # Scope to the mobile-only Profile button so the test is not
        # satisfied by some unrelated 'Profile' string on the page.
        idx = content.find('id="mobile-profile-link"')
        self.assertNotEqual(idx, -1, "mobile-profile-link element missing")
        tag_end = content.find(">", idx)
        tag_start = content.rfind("<", 0, idx)
        link_tag = content[tag_start:tag_end + 1]
        self.assertIn('href="/account/#profile"', link_tag)

    def test_anonymous_mobile_menu_has_no_profile_link(self):
        # Use a public page that includes the header so the anonymous block
        # is rendered. The home page includes the public header.
        response = self.client.get("/")
        content = response.content.decode()
        self.assertNotIn('id="mobile-profile-link"', content)


class ContactsExportRoundTripTest(TestCase):
    """Saving a name on /account/ surfaces in the contacts API export.

    The export side already exists from issue #431. This test pins that
    the round-trip from the consolidated profile UI -> the API consumer
    keeps working after issue #447.
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

        # Save name through the consolidated profile form (POST target
        # is still ``/account/profile``).
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
    """Importing a name through the API pre-fills the inline /account/ form.

    Pins the contract on the import side (issue #437) end-to-end with the
    consolidated profile UI on /account/ (issue #447).
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

    def test_imported_name_pre_fills_account_profile_form(self):
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

        # Log in as Bob and load the consolidated /account/ page.
        self.client.force_login(bob)
        response = self.client.get("/account/")
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
