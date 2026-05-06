"""Studio admin views for sprints (issue #432)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Sprint

User = get_user_model()


class SprintAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def test_sprint_list_requires_staff(self):
        # Anonymous: redirect to login.
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

        # Non-staff authenticated: 403.
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # Staff: 200.
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 200)


class SprintListRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.s1 = Sprint.objects.create(
            name='Alpha sprint', slug='alpha',
            start_date=datetime.date(2026, 4, 1),
        )
        cls.s2 = Sprint.objects.create(
            name='Beta sprint', slug='beta',
            start_date=datetime.date(2026, 6, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_list_renders_sprint_names(self):
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 200)
        # Both sprint names appear inside the list-row link, not just
        # somewhere on the page. The template wraps each name in an
        # ``<a>`` whose href is the detail URL.
        self.assertContains(
            response,
            f'<a href="/studio/sprints/{self.s1.pk}/" class="text-accent hover:underline">Alpha sprint</a>',
            html=True,
        )
        self.assertContains(
            response,
            f'<a href="/studio/sprints/{self.s2.pk}/" class="text-accent hover:underline">Beta sprint</a>',
            html=True,
        )


class SprintCreateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_create_post_creates_row_and_redirects(self):
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Summer 2026',
            'slug': 'summer-2026',
            'start_date': '2026-07-01',
            'duration_weeks': '6',
            'status': 'draft',
        })
        self.assertEqual(Sprint.objects.count(), before + 1)
        sprint = Sprint.objects.get(slug='summer-2026')
        self.assertEqual(sprint.name, 'Summer 2026')
        self.assertEqual(sprint.duration_weeks, 6)
        self.assertEqual(sprint.start_date, datetime.date(2026, 7, 1))
        self.assertRedirects(response, f'/studio/sprints/{sprint.pk}/')

    def test_sprint_create_rejects_duplicate_slug(self):
        Sprint.objects.create(
            name='Existing', slug='dup',
            start_date=datetime.date(2026, 5, 1),
        )
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Another',
            'slug': 'dup',
            'start_date': '2026-06-01',
            'duration_weeks': '6',
            'status': 'draft',
        })
        # Form re-renders with an error, no new row.
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        self.assertEqual(Sprint.objects.count(), before)


class SprintEditTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='To edit', slug='to-edit',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status='draft',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_edit_updates_status(self):
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/edit', {
            'name': self.sprint.name,
            'slug': self.sprint.slug,
            'start_date': self.sprint.start_date.isoformat(),
            'duration_weeks': str(self.sprint.duration_weeks),
            'status': 'active',
        })
        self.assertEqual(response.status_code, 302)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'active')


class SprintDetailEditAccessTest(TestCase):
    """Detail / edit / new pages: 200 for staff, 403/redirect otherwise."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )

    def test_staff_can_reach_all_sprint_pages(self):
        self.client.login(email='staff@test.com', password='pw')
        for url in [
            '/studio/sprints/',
            '/studio/sprints/new',
            f'/studio/sprints/{self.sprint.pk}/',
            f'/studio/sprints/{self.sprint.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, msg=f'{url} -> {response.status_code}')

    def test_non_staff_cannot_reach_sprint_pages(self):
        self.client.login(email='member@test.com', password='pw')
        for url in [
            '/studio/sprints/',
            '/studio/sprints/new',
            f'/studio/sprints/{self.sprint.pk}/',
            f'/studio/sprints/{self.sprint.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, msg=f'{url} -> {response.status_code}')

    def test_anonymous_redirected_to_login(self):
        for url in [
            '/studio/sprints/',
            '/studio/sprints/new',
            f'/studio/sprints/{self.sprint.pk}/',
            f'/studio/sprints/{self.sprint.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, msg=f'{url} -> {response.status_code}')
            self.assertIn('/accounts/login/', response['Location'])
