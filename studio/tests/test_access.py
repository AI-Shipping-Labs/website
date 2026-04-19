"""Tests for studio access control.

Verifies that:
- Staff users can access all studio pages
- Non-staff authenticated users receive 403
- Anonymous users are redirected to login
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

User = get_user_model()


@tag('core')
class StudioAccessControlTest(TestCase):
    """Test that studio pages are staff-only."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    # ----------------------------------------------------------------
    # Anonymous users should be redirected to login
    # ----------------------------------------------------------------

    def test_anonymous_dashboard_redirects(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_anonymous_courses_redirects(self):
        response = self.client.get('/studio/courses/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_anonymous_articles_redirects(self):
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_anonymous_events_redirects(self):
        response = self.client.get('/studio/events/')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_recordings_redirects(self):
        response = self.client.get('/studio/recordings/')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_campaigns_redirects(self):
        response = self.client.get('/studio/campaigns/')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_users_redirects(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_downloads_redirects(self):
        response = self.client.get('/studio/downloads/')
        self.assertEqual(response.status_code, 302)

    def test_anonymous_projects_redirects(self):
        response = self.client.get('/studio/projects/')
        self.assertEqual(response.status_code, 302)

    # ----------------------------------------------------------------
    # Non-staff authenticated users should get 403
    # ----------------------------------------------------------------

    def test_non_staff_dashboard_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_courses_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/courses/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_articles_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_events_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/events/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_recordings_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/recordings/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_campaigns_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/campaigns/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_users_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_downloads_forbidden(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario12RegularMemberCannotAccessStudio::test_non_staff_member_is_denied_studio_access
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/downloads/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_projects_forbidden(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/projects/')
        self.assertEqual(response.status_code, 403)

    # ----------------------------------------------------------------
    # Staff users should get 200
    # ----------------------------------------------------------------

    def test_staff_dashboard_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)

    def test_staff_courses_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/courses/')
        self.assertEqual(response.status_code, 200)

    def test_staff_articles_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 200)

    def test_staff_events_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/events/')
        self.assertEqual(response.status_code, 200)

    def test_staff_recordings_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/recordings/')
        self.assertEqual(response.status_code, 200)

    def test_staff_campaigns_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/campaigns/')
        self.assertEqual(response.status_code, 200)

    def test_staff_users_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)

    def test_staff_downloads_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/downloads/')
        self.assertEqual(response.status_code, 200)

    def test_staff_projects_accessible(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/projects/')
        self.assertEqual(response.status_code, 200)

    # ----------------------------------------------------------------
    # Login redirect includes next parameter
    # ----------------------------------------------------------------

    def test_redirect_includes_next_param(self):
        response = self.client.get('/studio/')
        self.assertIn('next=/studio/', response.url)

    def test_redirect_includes_next_for_courses(self):
        response = self.client.get('/studio/courses/')
        self.assertIn('next=/studio/courses/', response.url)
