"""Smoke tests for the Django Q admin pages.

The list and detail pages themselves are provided by Django Q (third-party).
These tests exist solely to assert that:

- Staff users can reach each Django Q admin URL (so ``staff_member_required``
  + URL wiring stays intact, and the admin index links to all four sections).
- Anonymous visitors are bounced to the admin login (so we never accidentally
  leak job data through a misconfigured admin route).

This replaces the wide click-around assertions previously done via Playwright
in ``playwright_tests/test_background_jobs.py`` (issue #264).
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

User = get_user_model()


DJANGO_Q_ADMIN_URLS = [
    '/admin/django_q/ormq/',
    '/admin/django_q/success/',
    '/admin/django_q/failure/',
    '/admin/django_q/schedule/',
]


class DjangoQAdminAccessTest(TestCase):
    """Smoke-test the four Django Q admin sections.

    These pages are owned by django_q itself; we only verify that our admin
    wiring (``admin.site.urls``) exposes them to staff and protects them from
    anonymous visitors.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )

    def test_staff_can_view_each_django_q_admin_list(self):
        client = Client()
        client.login(email='admin@test.com', password='testpass')
        for url in DJANGO_Q_ADMIN_URLS:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(
                    response.status_code, 200,
                    f'staff cannot reach {url} (got {response.status_code})',
                )

    def test_anonymous_redirected_from_each_django_q_admin_list(self):
        client = Client()
        for url in DJANGO_Q_ADMIN_URLS:
            with self.subTest(url=url):
                response = client.get(url)
                # Django admin returns 302 to /admin/login/ for anonymous users
                self.assertEqual(
                    response.status_code, 302,
                    f'anonymous user not redirected from {url}',
                )
                self.assertIn('login', response.url.lower())

    def test_admin_index_links_to_all_four_django_q_sections(self):
        """The Django admin index should expose all four Django Q sections so
        staff can navigate to them. This replaces the Playwright assertion
        that the admin index shows links to ormq/success/failure/schedule.
        """
        client = Client()
        client.login(email='admin@test.com', password='testpass')
        response = client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        for url in DJANGO_Q_ADMIN_URLS:
            self.assertContains(
                response, f'href="{url}"',
                msg_prefix=f'admin index missing link to {url}: ',
            )
