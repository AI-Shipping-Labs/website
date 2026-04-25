"""Tests for studio access control.

Verifies that:
- Staff users can access all studio pages
- Non-staff authenticated users receive 403
- Anonymous users are redirected to login (with next= original path)
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from tests.fixtures import StaffUserMixin

User = get_user_model()


@tag('core')
class StudioAccessControlTest(StaffUserMixin, TestCase):
    """Test that studio pages are staff-only."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    # ----------------------------------------------------------------
    # Anonymous users should be redirected to login (with next= param)
    # ----------------------------------------------------------------

    def _assert_anonymous_redirect(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn(f'next={path}', response.url)

    def test_anonymous_dashboard_redirects(self):
        self._assert_anonymous_redirect('/studio/')

    def test_anonymous_courses_redirects(self):
        self._assert_anonymous_redirect('/studio/courses/')

    def test_anonymous_articles_redirects(self):
        self._assert_anonymous_redirect('/studio/articles/')

    def test_anonymous_events_redirects(self):
        self._assert_anonymous_redirect('/studio/events/')

    def test_anonymous_recordings_redirects(self):
        self._assert_anonymous_redirect('/studio/recordings/')

    def test_anonymous_campaigns_redirects(self):
        self._assert_anonymous_redirect('/studio/campaigns/')

    def test_anonymous_users_redirects(self):
        self._assert_anonymous_redirect('/studio/users/')

    def test_anonymous_downloads_redirects(self):
        self._assert_anonymous_redirect('/studio/downloads/')

    def test_anonymous_projects_redirects(self):
        self._assert_anonymous_redirect('/studio/projects/')

    # ----------------------------------------------------------------
    # Non-staff authenticated users should get 403 with no leaked data
    # ----------------------------------------------------------------

    def _assert_non_staff_forbidden(self, path):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get(path)
        self.assertEqual(response.status_code, 403)
        # The studio list pages all render <table>; its absence in a 403 body
        # confirms the gate fired before the data was rendered.
        self.assertNotContains(response, '<table', status_code=403)

    def test_non_staff_dashboard_forbidden(self):
        self._assert_non_staff_forbidden('/studio/')

    def test_non_staff_courses_forbidden(self):
        self._assert_non_staff_forbidden('/studio/courses/')

    def test_non_staff_articles_forbidden(self):
        self._assert_non_staff_forbidden('/studio/articles/')

    def test_non_staff_events_forbidden(self):
        self._assert_non_staff_forbidden('/studio/events/')

    def test_non_staff_recordings_forbidden(self):
        self._assert_non_staff_forbidden('/studio/recordings/')

    def test_non_staff_campaigns_forbidden(self):
        self._assert_non_staff_forbidden('/studio/campaigns/')

    def test_non_staff_users_forbidden(self):
        self._assert_non_staff_forbidden('/studio/users/')

    def test_non_staff_downloads_forbidden(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario12RegularMemberCannotAccessStudio::test_non_staff_member_is_denied_studio_access
        self._assert_non_staff_forbidden('/studio/downloads/')

    def test_non_staff_projects_forbidden(self):
        self._assert_non_staff_forbidden('/studio/projects/')

    # ----------------------------------------------------------------
    # Staff users should get 200 with the correct studio template
    # ----------------------------------------------------------------

    def _assert_staff_200(self, path, template):
        self.client.login(**self.staff_credentials)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, template)

    def test_staff_dashboard_accessible(self):
        self._assert_staff_200('/studio/', 'studio/dashboard.html')

    def test_staff_courses_accessible(self):
        self._assert_staff_200('/studio/courses/', 'studio/courses/list.html')

    def test_staff_articles_accessible(self):
        self._assert_staff_200('/studio/articles/', 'studio/articles/list.html')

    def test_staff_events_accessible(self):
        self._assert_staff_200('/studio/events/', 'studio/events/list.html')

    def test_staff_recordings_accessible(self):
        self._assert_staff_200('/studio/recordings/', 'studio/recordings/list.html')

    def test_staff_campaigns_accessible(self):
        self._assert_staff_200('/studio/campaigns/', 'studio/campaigns/list.html')

    def test_staff_users_accessible(self):
        self._assert_staff_200('/studio/users/', 'studio/users/list.html')

    def test_staff_downloads_accessible(self):
        self._assert_staff_200('/studio/downloads/', 'studio/downloads/list.html')

    def test_staff_projects_accessible(self):
        self._assert_staff_200('/studio/projects/', 'studio/projects/list.html')
