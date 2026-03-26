from django.test import TestCase, override_settings


@override_settings(APPEND_SLASH=True)
class RemoveTrailingSlashMiddlewareTest(TestCase):

    def test_trailing_slash_redirects(self):
        response = self.client.get('/courses/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/courses')

    def test_no_trailing_slash_passes_through(self):
        response = self.client.get('/courses')
        self.assertEqual(response.status_code, 200)

    def test_root_url_not_redirected(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_query_string_preserved(self):
        response = self.client.get('/courses/?page=2')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/courses?page=2')

    def test_deep_path_trailing_slash_redirects(self):
        """Trailing slash on nested paths redirects too."""
        response = self.client.get('/blog/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/blog')

    def test_admin_not_redirected(self):
        """Admin paths keep trailing slashes (Django admin convention)."""
        response = self.client.get('/admin/login/')
        self.assertNotEqual(response.status_code, 301)

    def test_accounts_not_redirected(self):
        """Accounts paths keep trailing slashes (allauth convention)."""
        response = self.client.get('/accounts/login/')
        self.assertNotEqual(response.status_code, 301)

    def test_studio_not_redirected(self):
        """Studio paths keep trailing slashes."""
        response = self.client.get('/studio/courses/')
        self.assertNotEqual(response.status_code, 301)

    def test_accounts_no_slash_appends_slash(self):
        """APPEND_SLASH adds trailing slash for accounts paths."""
        response = self.client.get('/accounts/login')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/accounts/login/')
