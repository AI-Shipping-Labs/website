"""Tests for the Studio call-host management views (#870)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from community.models import CallHost
from tests.fixtures import StaffUserMixin

User = get_user_model()


@tag('core')
class StudioCallHostAccessTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.host = CallHost.objects.get(slug='valeria')

    def test_list_requires_login_anonymous_redirected(self):
        response = self.client.get('/studio/call-hosts/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_list_forbidden_for_non_staff(self):
        self.client.force_login(self.member)
        response = self.client.get('/studio/call-hosts/')
        self.assertEqual(response.status_code, 403)

    def test_edit_forbidden_for_non_staff_and_no_side_effect(self):
        self.client.force_login(self.member)
        original = self.host.booking_url
        response = self.client.post(
            f'/studio/call-hosts/{self.host.pk}/edit',
            {'name': 'Hacked', 'booking_url': 'https://evil.example', 'capacity': '99'},
        )
        self.assertEqual(response.status_code, 403)
        self.host.refresh_from_db()
        self.assertEqual(self.host.booking_url, original)
        self.assertNotEqual(self.host.name, 'Hacked')


@tag('core')
class StudioCallHostCrudTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.host = CallHost.objects.get(slug='valeria')

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_shows_hosts_and_availability(self):
        CallHost.objects.filter(slug='valeria').update(
            is_active=True, capacity=5, current_load=0,
        )
        CallHost.objects.filter(slug='alexey').update(
            is_active=True, capacity=0, current_load=0,
        )
        response = self.client.get('/studio/call-hosts/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/call_hosts/list.html')
        self.assertContains(response, 'Valeriia Kuka')
        self.assertContains(response, 'Available')
        self.assertContains(response, 'Not available')

    def test_edit_saves_booking_url_and_capacity(self):
        response = self.client.post(
            f'/studio/call-hosts/{self.host.pk}/edit',
            {
                'name': 'Valeriia Kuka',
                'slug': 'valeria',
                'role_label': 'Co-founder',
                'photo_url': '',
                'booking_url': 'https://calendar.app.google/NEW',
                'capacity': '8',
                'current_load': '2',
                'order': '2',
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.host.refresh_from_db()
        self.assertEqual(self.host.booking_url, 'https://calendar.app.google/NEW')
        self.assertEqual(self.host.capacity, 8)
        self.assertEqual(self.host.current_load, 2)
        self.assertTrue(self.host.is_active)
        self.assertEqual(self.host.role_label, 'Co-founder')

    def test_edit_unchecking_active_deactivates(self):
        CallHost.objects.filter(pk=self.host.pk).update(is_active=True)
        response = self.client.post(
            f'/studio/call-hosts/{self.host.pk}/edit',
            {
                'name': 'Valeriia Kuka',
                'slug': 'valeria',
                'booking_url': 'https://example.com',
                'capacity': '5',
                'current_load': '0',
                'order': '2',
                # is_active omitted -> unchecked
            },
        )
        self.assertEqual(response.status_code, 302)
        self.host.refresh_from_db()
        self.assertFalse(self.host.is_active)

    def test_lowering_capacity_to_zero_makes_unavailable(self):
        CallHost.objects.filter(pk=self.host.pk).update(
            is_active=True, capacity=5, current_load=0,
        )
        self.client.post(
            f'/studio/call-hosts/{self.host.pk}/edit',
            {
                'name': 'Valeriia Kuka',
                'slug': 'valeria',
                'booking_url': 'https://example.com',
                'capacity': '0',
                'current_load': '0',
                'order': '2',
                'is_active': 'on',
            },
        )
        self.host.refresh_from_db()
        self.assertFalse(self.host.is_available)
