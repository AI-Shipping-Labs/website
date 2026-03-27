"""Tests for studio course access management views.

Verifies:
- Course access list view shows all access records
- Grant access by email (POST)
- Revoke granted access (POST)
- Cannot revoke purchased access
- Staff-only access (non-staff get 403, anonymous redirected)
- Duplicate grant shows appropriate message
- Grant with nonexistent email shows error
- Grant with empty email shows error
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.models import Course, CourseAccess

User = get_user_model()


class StudioCourseAccessListTest(TestCase):
    """Test course access list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Test Course', slug='test-course', status='published',
        )

    def test_access_list_returns_200(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertEqual(response.status_code, 200)

    def test_access_list_uses_correct_template(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertTemplateUsed(response, 'studio/courses/access_list.html')

    def test_access_list_shows_granted_access(self):
        user = User.objects.create_user(email='granted@test.com', password='pass')
        CourseAccess.objects.create(
            user=user, course=self.course, access_type='granted',
            granted_by=self.staff,
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'granted@test.com')

    def test_access_list_shows_purchased_access(self):
        user = User.objects.create_user(email='buyer@test.com', password='pass')
        CourseAccess.objects.create(
            user=user, course=self.course, access_type='purchased',
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'buyer@test.com')
        self.assertContains(response, 'Purchased')

    def test_access_list_shows_both_types(self):
        granted_user = User.objects.create_user(email='g@test.com', password='pass')
        purchased_user = User.objects.create_user(email='p@test.com', password='pass')
        CourseAccess.objects.create(
            user=granted_user, course=self.course, access_type='granted',
            granted_by=self.staff,
        )
        CourseAccess.objects.create(
            user=purchased_user, course=self.course, access_type='purchased',
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'g@test.com')
        self.assertContains(response, 'p@test.com')

    def test_access_list_empty_state(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'No individual access records')

    def test_access_list_shows_granted_by(self):
        user = User.objects.create_user(email='u@test.com', password='pass')
        CourseAccess.objects.create(
            user=user, course=self.course, access_type='granted',
            granted_by=self.staff,
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'staff@test.com')

    def test_access_list_nonexistent_course_returns_404(self):
        response = self.client.get('/studio/courses/99999/access/')
        self.assertEqual(response.status_code, 404)

    def test_access_list_has_breadcrumb_to_course_edit(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, f'/studio/courses/{self.course.pk}/edit')

    def test_access_list_revoke_button_only_for_granted(self):
        granted_user = User.objects.create_user(email='g@test.com', password='pass')
        purchased_user = User.objects.create_user(email='p@test.com', password='pass')
        ga = CourseAccess.objects.create(
            user=granted_user, course=self.course, access_type='granted',
            granted_by=self.staff,
        )
        CourseAccess.objects.create(
            user=purchased_user, course=self.course, access_type='purchased',
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        content = response.content.decode()
        # The revoke URL should appear for the granted access
        self.assertIn(f'/access/{ga.pk}/revoke/', content)
        # The word "Revoke" should appear once (only for granted)
        self.assertEqual(content.count('Revoke</button>'), 1)


class StudioCourseAccessGrantTest(TestCase):
    """Test granting course access."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Grant Course', slug='grant-course', status='published',
        )
        self.target_user = User.objects.create_user(
            email='target@test.com', password='pass',
        )

    def test_grant_access_creates_record(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'target@test.com'},
        )
        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.target_user, course=self.course, access_type='granted',
            ).exists()
        )

    def test_grant_access_sets_granted_by(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'target@test.com'},
        )
        access = CourseAccess.objects.get(user=self.target_user, course=self.course)
        self.assertEqual(access.granted_by, self.staff)

    def test_grant_access_redirects_to_access_list(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'target@test.com'},
        )
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/access/',
            fetch_redirect_response=False,
        )

    def test_grant_access_nonexistent_email_shows_error(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'nobody@test.com'},
            follow=True,
        )
        self.assertContains(response, 'No user found with email')

    def test_grant_access_empty_email_shows_error(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': ''},
            follow=True,
        )
        self.assertContains(response, 'Please provide an email address')

    def test_grant_access_duplicate_shows_info(self):
        CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='granted', granted_by=self.staff,
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'target@test.com'},
            follow=True,
        )
        self.assertContains(response, 'already has granted access')
        # Should not create a second record
        self.assertEqual(
            CourseAccess.objects.filter(user=self.target_user, course=self.course).count(),
            1,
        )

    def test_grant_access_duplicate_purchased_shows_info(self):
        CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='purchased',
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'target@test.com'},
            follow=True,
        )
        self.assertContains(response, 'already has purchased access')

    def test_grant_requires_post(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/access/grant/',
        )
        self.assertEqual(response.status_code, 405)

    def test_grant_nonexistent_course_returns_404(self):
        response = self.client.post(
            '/studio/courses/99999/access/grant/',
            {'email': 'target@test.com'},
        )
        self.assertEqual(response.status_code, 404)


class StudioCourseAccessRevokeTest(TestCase):
    """Test revoking course access."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Revoke Course', slug='revoke-course', status='published',
        )
        self.target_user = User.objects.create_user(
            email='target@test.com', password='pass',
        )

    def test_revoke_granted_access_deletes_record(self):
        access = CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='granted', granted_by=self.staff,
        )
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
        )
        self.assertFalse(CourseAccess.objects.filter(pk=access.pk).exists())

    def test_revoke_redirects_to_access_list(self):
        access = CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='granted', granted_by=self.staff,
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
        )
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/access/',
            fetch_redirect_response=False,
        )

    def test_revoke_purchased_access_not_allowed(self):
        access = CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='purchased',
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
            follow=True,
        )
        # Record should still exist
        self.assertTrue(CourseAccess.objects.filter(pk=access.pk).exists())
        self.assertContains(response, 'Only granted access can be revoked')

    def test_revoke_shows_success_message(self):
        access = CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='granted', granted_by=self.staff,
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
            follow=True,
        )
        self.assertContains(response, 'Access revoked for target@test.com')

    def test_revoke_requires_post(self):
        access = CourseAccess.objects.create(
            user=self.target_user, course=self.course,
            access_type='granted', granted_by=self.staff,
        )
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
        )
        self.assertEqual(response.status_code, 405)

    def test_revoke_nonexistent_access_returns_404(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/99999/revoke/',
        )
        self.assertEqual(response.status_code, 404)

    def test_revoke_wrong_course_returns_404(self):
        """Access ID belongs to a different course."""
        other_course = Course.objects.create(
            title='Other', slug='other-course', status='published',
        )
        access = CourseAccess.objects.create(
            user=self.target_user, course=other_course,
            access_type='granted', granted_by=self.staff,
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
        )
        self.assertEqual(response.status_code, 404)


class StudioCourseAccessPermissionTest(TestCase):
    """Test access control for course access management views."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Perm Course', slug='perm-course', status='published',
        )

    def test_anonymous_access_list_redirects_to_login(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_anonymous_grant_redirects_to_login(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'test@test.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_access_list_forbidden(self):
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_grant_forbidden(self):
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'test@test.com'},
        )
        self.assertEqual(response.status_code, 403)

    def test_non_staff_revoke_forbidden(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        target = User.objects.create_user(
            email='target@test.com', password='testpass',
        )
        access = CourseAccess.objects.create(
            user=target, course=self.course,
            access_type='granted', granted_by=staff,
        )
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/{access.pk}/revoke/',
        )
        self.assertEqual(response.status_code, 403)


class StudioCourseEditAccessLinkTest(TestCase):
    """Test that the course edit page contains a link to manage access."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Link Course', slug='link-course', status='draft',
        )

    def test_edit_page_has_manage_access_link(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'Manage Access')
        self.assertContains(
            response,
            f'/studio/courses/{self.course.pk}/access/',
        )
