"""Unit tests for ``accounts.return_context`` helpers (issues #485, #519)."""

from django.test import RequestFactory, TestCase, tag

from accounts.return_context import (
    LOGOUT_REDIRECT_EXCLUDED_PREFIXES,
    append_next,
    get_next_url,
    sanitize_next_url,
    should_skip_logout_redirect,
)


@tag('core')
class SanitizeNextUrlTest(TestCase):
    def test_safe_local_path_passes_through(self):
        self.assertEqual(sanitize_next_url("/events/foo"), "/events/foo")

    def test_safe_path_with_query_and_fragment(self):
        self.assertEqual(
            sanitize_next_url("/pricing?tier=main#plans"),
            "/pricing?tier=main#plans",
        )

    def test_absolute_url_is_rejected(self):
        self.assertEqual(
            sanitize_next_url("https://evil.example.com/x"), "/"
        )

    def test_protocol_relative_is_rejected(self):
        self.assertEqual(
            sanitize_next_url("//evil.example.com/x"), "/"
        )

    def test_backslash_is_rejected(self):
        self.assertEqual(sanitize_next_url("/foo\\bar"), "/")

    def test_control_char_is_rejected(self):
        self.assertEqual(sanitize_next_url("/foo\nbar"), "/")

    def test_non_string_is_rejected(self):
        self.assertEqual(sanitize_next_url(None), "/")

    def test_empty_string_uses_default(self):
        self.assertEqual(sanitize_next_url("", default="/x"), "/x")


@tag('core')
class ShouldSkipLogoutRedirectTest(TestCase):
    """Coverage for the exclusion list used by sign-out (issue #519)."""

    def test_homepage_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/"))

    def test_event_detail_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/events/return-ctx-event"))

    def test_course_detail_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/courses/demo"))

    def test_course_unit_is_not_excluded(self):
        self.assertFalse(
            should_skip_logout_redirect("/courses/demo/intro/lesson")
        )

    def test_workshop_detail_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/workshops/demo"))

    def test_blog_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/blog/some-slug"))

    def test_pricing_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/pricing"))

    def test_account_root_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/account"))

    def test_account_subpath_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/account/profile"))

    def test_accounts_login_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/accounts/login/"))

    def test_studio_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/studio/articles/"))

    def test_admin_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/admin/"))

    def test_notifications_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/notifications"))

    def test_notifications_subpath_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect("/notifications/feed"))

    def test_query_string_does_not_bypass_exclusion(self):
        self.assertTrue(
            should_skip_logout_redirect("/studio?from=header")
        )

    def test_fragment_does_not_bypass_exclusion(self):
        self.assertTrue(
            should_skip_logout_redirect("/account/#profile")
        )

    def test_similar_prefix_is_not_excluded(self):
        """``/accounting`` shares ``account`` characters but is not excluded."""
        self.assertFalse(should_skip_logout_redirect("/accounting"))

    def test_similar_studio_prefix_is_not_excluded(self):
        self.assertFalse(should_skip_logout_redirect("/studios"))

    def test_non_path_input_is_excluded(self):
        """A string without a leading slash cannot be a safe redirect."""
        self.assertTrue(should_skip_logout_redirect("foo"))

    def test_non_string_is_excluded(self):
        self.assertTrue(should_skip_logout_redirect(None))

    def test_exclusion_list_contains_known_prefixes(self):
        """Defensive check: keep the constant in sync with the docstring."""
        self.assertIn("/account", LOGOUT_REDIRECT_EXCLUDED_PREFIXES)
        self.assertIn("/accounts", LOGOUT_REDIRECT_EXCLUDED_PREFIXES)
        self.assertIn("/studio", LOGOUT_REDIRECT_EXCLUDED_PREFIXES)
        self.assertIn("/admin", LOGOUT_REDIRECT_EXCLUDED_PREFIXES)
        self.assertIn("/notifications", LOGOUT_REDIRECT_EXCLUDED_PREFIXES)


@tag('core')
class AppendNextTest(TestCase):
    def test_appends_next_to_bare_url(self):
        self.assertEqual(
            append_next("/accounts/login/", "/events/foo"),
            "/accounts/login/?next=%2Fevents%2Ffoo",
        )

    def test_appends_with_existing_query(self):
        self.assertEqual(
            append_next("/accounts/login/?foo=1", "/events/foo"),
            "/accounts/login/?foo=1&next=%2Fevents%2Ffoo",
        )

    def test_drops_unsafe_next(self):
        self.assertEqual(
            append_next("/accounts/login/", "https://evil.example/x"),
            "/accounts/login/",
        )


@tag('core')
class GetNextUrlTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_reads_safe_next_from_query(self):
        request = self.factory.get("/accounts/logout/", {"next": "/events/foo"})
        self.assertEqual(get_next_url(request), "/events/foo")

    def test_falls_back_to_default_when_unsafe(self):
        request = self.factory.get(
            "/accounts/logout/",
            {"next": "https://evil.example/x"},
        )
        self.assertEqual(get_next_url(request, default="/"), "/")

    def test_reads_from_post_body_when_get_missing(self):
        request = self.factory.post(
            "/accounts/logout/",
            {"next": "/courses/demo"},
        )
        self.assertEqual(get_next_url(request), "/courses/demo")
