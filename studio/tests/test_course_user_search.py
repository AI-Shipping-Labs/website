"""Tests for the staff-only Studio user search JSON endpoint (issue #492).

Verifies:
- Endpoint requires staff (anonymous redirected, non-staff 403)
- Returns JSON with limited identity fields (id, email, name)
- Searches by email substring (case-insensitive)
- Searches by exact numeric user ID
- Empty query returns empty list
- Returns 404 for nonexistent course
- Capped at 10 results
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from content.models import Course

User = get_user_model()


@tag('core')
class StudioCourseUserSearchTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Search Course', slug='search-course', status='published',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@example.com', password='pw', first_name='Alice', last_name='Adams',
        )
        cls.bob = User.objects.create_user(
            email='bob@other.com', password='pw',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def _url(self, q=None, course_id=None):
        cid = course_id if course_id is not None else self.course.pk
        base = f'/studio/courses/{cid}/access/users/search/'
        if q is not None:
            return f'{base}?q={q}'
        return base

    def test_returns_json_with_results_key(self):
        response = self.client.get(self._url('alice'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = json.loads(response.content)
        self.assertIn('results', data)

    def test_search_by_email_substring(self):
        response = self.client.get(self._url('alice'))
        data = json.loads(response.content)
        emails = [r['email'] for r in data['results']]
        self.assertIn('alice@example.com', emails)
        self.assertNotIn('bob@other.com', emails)

    def test_search_by_email_substring_case_insensitive(self):
        response = self.client.get(self._url('ALICE'))
        data = json.loads(response.content)
        emails = [r['email'] for r in data['results']]
        self.assertIn('alice@example.com', emails)

    def test_search_by_numeric_user_id_returns_user(self):
        response = self.client.get(self._url(str(self.bob.pk)))
        data = json.loads(response.content)
        ids = [r['id'] for r in data['results']]
        self.assertIn(self.bob.pk, ids)

    def test_results_only_contain_limited_fields(self):
        response = self.client.get(self._url('alice'))
        data = json.loads(response.content)
        result = next(r for r in data['results'] if r['email'] == 'alice@example.com')
        self.assertEqual(set(result.keys()), {'id', 'email', 'name'})
        self.assertEqual(result['name'], 'Alice Adams')
        # No sensitive fields like password / is_staff / stripe ids
        self.assertNotIn('password', result)
        self.assertNotIn('is_staff', result)
        self.assertNotIn('stripe_customer_id', result)

    def test_empty_query_returns_empty_results(self):
        response = self.client.get(self._url(''))
        data = json.loads(response.content)
        self.assertEqual(data['results'], [])

    def test_no_query_param_returns_empty_results(self):
        response = self.client.get(self._url())
        data = json.loads(response.content)
        self.assertEqual(data['results'], [])

    def test_results_capped_at_10(self):
        for i in range(15):
            User.objects.create_user(email=f'bulk{i}@cap.test', password='pw')
        response = self.client.get(self._url('bulk'))
        data = json.loads(response.content)
        self.assertEqual(len(data['results']), 10)

    def test_nonexistent_course_returns_404(self):
        response = self.client.get(
            '/studio/courses/99999/access/users/search/?q=alice',
        )
        self.assertEqual(response.status_code, 404)


@tag('core')
class StudioCourseUserSearchPermissionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Search Course', slug='search-course-perm', status='published',
        )

    def setUp(self):
        self.client = Client()

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/access/users/search/?q=a',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_forbidden(self):
        User.objects.create_user(email='regular@test.com', password='pw', is_staff=False)
        self.client.login(email='regular@test.com', password='pw')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/access/users/search/?q=a',
        )
        self.assertEqual(response.status_code, 403)

    def test_non_staff_does_not_leak_users(self):
        """Even with a valid user listed, a non-staff caller must not see them."""
        User.objects.create_user(email='target@test.com', password='pw')
        User.objects.create_user(email='regular@test.com', password='pw', is_staff=False)
        self.client.login(email='regular@test.com', password='pw')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/access/users/search/?q=target',
        )
        # 403 body must not contain the email
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'target@test.com', response.content)
