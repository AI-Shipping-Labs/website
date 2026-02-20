from django.test import TestCase
from django.contrib.admin.sites import AdminSite

from accounts.admin import UserAdmin
from accounts.models import User


class UserAdminTest(TestCase):
    """Tests for the User admin configuration."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com", password="adminpass123"
        )
        self.client.force_login(self.admin_user)

    def test_admin_user_list_returns_200(self):
        response = self.client.get("/admin/accounts/user/")
        self.assertEqual(response.status_code, 200)

    def test_admin_user_list_displays_columns(self):
        """Admin list should show email, tier, email_verified, date joined."""
        admin_instance = UserAdmin(User, AdminSite())
        self.assertIn("email", admin_instance.list_display)
        self.assertIn("tier", admin_instance.list_display)
        self.assertIn("email_verified", admin_instance.list_display)
        self.assertIn("date_joined", admin_instance.list_display)

    def test_admin_user_add_page_returns_200(self):
        response = self.client.get("/admin/accounts/user/add/")
        self.assertEqual(response.status_code, 200)

    def test_admin_user_change_page_returns_200(self):
        user = User.objects.create_user(email="test@example.com")
        response = self.client.get(f"/admin/accounts/user/{user.pk}/change/")
        self.assertEqual(response.status_code, 200)

    def test_admin_user_list_contains_user_email(self):
        User.objects.create_user(email="testuser@example.com")
        response = self.client.get("/admin/accounts/user/")
        content = response.content.decode()
        self.assertIn("testuser@example.com", content)
