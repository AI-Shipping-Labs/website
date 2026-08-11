"""Studio Call profile CRUD tests (#1404)."""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import Client, TestCase, tag

from community.models import STATUS_CANCELED, BookedCall, CallHost
from tests.fixtures import StaffUserMixin

User = get_user_model()


def _profile_payload(**overrides):
    payload = {
        'name': 'Jordan Lee',
        'slug': 'jordan-lee',
        'role_label': 'AI product coach',
        'photo_url': 'https://example.com/jordan.jpg',
        'booking_url': 'https://calendar.app.google/jordan',
        'order': '3',
        'is_active': 'on',
    }
    payload.update(overrides)
    return payload


@tag('core')
class StudioCallProfileAccessTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='member-call-profiles@test.com', password='pw',
        )
        cls.profile = CallHost.objects.get(slug='valeria')

    def test_anonymous_is_redirected_from_every_studio_route(self):
        paths = [
            '/studio/call-hosts/',
            '/studio/call-hosts/new',
            f'/studio/call-hosts/{self.profile.pk}/edit',
            f'/studio/call-hosts/{self.profile.pk}/delete',
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.post(path) if path.endswith('/delete') else self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_cannot_create_edit_or_delete(self):
        self.client.force_login(self.member)
        before = CallHost.objects.count()
        responses = [
            self.client.post('/studio/call-hosts/new', _profile_payload()),
            self.client.post(
                f'/studio/call-hosts/{self.profile.pk}/edit',
                _profile_payload(name='Unauthorized'),
            ),
            self.client.post(f'/studio/call-hosts/{self.profile.pk}/delete'),
        ]
        self.assertEqual([response.status_code for response in responses], [403, 403, 403])
        self.assertEqual(CallHost.objects.count(), before)
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.name, 'Unauthorized')

    def test_delete_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(
            f'/studio/call-hosts/{self.profile.pk}/delete',
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(CallHost.objects.filter(pk=self.profile.pk).exists())


@tag('core')
class StudioCallProfileCrudTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile = CallHost.objects.get(slug='valeria')

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_uses_call_profile_contract_and_order(self):
        CallHost.objects.create(
            name='First profile',
            slug='first-profile',
            booking_url='https://example.com/first',
            is_active=True,
            order=0,
            capacity=99,
            current_load=88,
        )
        response = self.client.get('/studio/call-hosts/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Call profiles')
        self.assertContains(response, 'New call profile')
        self.assertContains(response, 'Profile')
        self.assertContains(response, 'Booking link')
        self.assertContains(response, 'Visibility')
        self.assertContains(response, 'Shown')
        self.assertNotContains(response, 'Capacity')
        self.assertNotContains(response, 'Current load')
        self.assertNotContains(response, 'Availability')
        hosts = list(response.context['hosts'])
        self.assertEqual(hosts[0].slug, 'first-profile')

    def test_list_uses_owned_table_badge_and_focus_patterns(self):
        source = (
            Path(__file__).resolve().parents[2]
            / 'templates/studio/call_hosts/list.html'
        ).read_text()
        self.assertIn("studio_list_class 'thead'", source)
        self.assertEqual(source.count("studio_list_class 'th'"), 4)
        self.assertNotIn('tracking-wider', source)
        self.assertIn(
            'hover:underline break-all focus-visible:outline-none '
            'focus-visible:ring-2 focus-visible:ring-accent '
            'focus-visible:ring-offset-2 focus-visible:ring-offset-background',
            source,
        )
        self.assertIn("studio_status_badge 'active' 'Shown'", source)
        self.assertIn("studio_status_badge 'reviewed' 'Hidden'", source)
        self.assertNotIn('bg-green-500/20 text-green-400', source)

        form_source = (
            Path(__file__).resolve().parents[2]
            / 'templates/studio/call_hosts/form.html'
        ).read_text()
        self.assertIn(
            'Optional. Leave blank to omit the profile photo.',
            form_source,
        )
        self.assertNotIn('use the default profile photo', form_source)

    def test_list_create_and_edit_reject_unsupported_methods_without_mutation(self):
        before = (CallHost.objects.count(), self.profile.name)
        list_response = self.client.post('/studio/call-hosts/')
        create_response = self.client.put(
            '/studio/call-hosts/new',
            data='name=Unsupported',
            content_type='application/x-www-form-urlencoded',
        )
        edit_response = self.client.delete(
            f'/studio/call-hosts/{self.profile.pk}/edit',
        )
        self.assertEqual(list_response.status_code, 405)
        self.assertEqual(create_response.status_code, 405)
        self.assertEqual(edit_response.status_code, 405)
        self.profile.refresh_from_db()
        self.assertEqual((CallHost.objects.count(), self.profile.name), before)

    def test_empty_list_uses_canonical_studio_empty_state(self):
        CallHost.objects.all().delete()
        response = self.client.get('/studio/call-hosts/')
        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        self.assertContains(response, 'No call profiles yet.')
        self.assertContains(response, 'New call profile')
        self.assertNotContains(response, '<table')

    def test_create_hidden_profile_generates_slug(self):
        payload = _profile_payload(slug='', booking_url='')
        payload.pop('is_active')
        response = self.client.post('/studio/call-hosts/new', payload, follow=True)
        self.assertRedirects(response, '/studio/call-hosts/')
        profile = CallHost.objects.get(slug='jordan-lee')
        self.assertFalse(profile.is_active)
        self.assertEqual(profile.booking_url, '')
        self.assertContains(response, 'Call profile “Jordan Lee” created.')
        self.assertContains(response, 'Hidden')

    def test_create_active_profile_requires_booking_url_and_preserves_values(self):
        response = self.client.post(
            '/studio/call-hosts/new',
            _profile_payload(booking_url='', role_label='Preserved role'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Booking URL is required when Show on Request a call is enabled.',
        )
        self.assertContains(response, 'value="Preserved role"')
        self.assertFalse(CallHost.objects.filter(slug='jordan-lee').exists())

    def test_form_rejects_duplicate_slug_invalid_urls_and_negative_order(self):
        response = self.client.post(
            '/studio/call-hosts/new',
            _profile_payload(
                slug='valeria',
                photo_url='ftp://example.com/photo.jpg',
                booking_url='javascript:alert(1)',
                order='-1',
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A call profile with this slug already exists.')
        self.assertContains(response, 'Enter a valid URL.', count=2)
        self.assertContains(response, 'Ensure this value is greater than or equal to 0.')

    def test_edit_updates_profile_but_preserves_legacy_capacity_fields(self):
        CallHost.objects.filter(pk=self.profile.pk).update(capacity=8, current_load=2)
        response = self.client.post(
            f'/studio/call-hosts/{self.profile.pk}/edit',
            _profile_payload(slug='valeria', name='Valeriia Updated'),
            follow=True,
        )
        self.assertRedirects(response, '/studio/call-hosts/')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.name, 'Valeriia Updated')
        self.assertEqual(self.profile.capacity, 8)
        self.assertEqual(self.profile.current_load, 2)
        self.assertContains(response, 'Call profile “Valeriia Updated” updated.')

    def test_invalid_edit_does_not_partially_mutate_profile(self):
        before = (
            self.profile.name,
            self.profile.booking_url,
            self.profile.is_active,
            self.profile.order,
        )
        response = self.client.post(
            f'/studio/call-hosts/{self.profile.pk}/edit',
            _profile_payload(
                slug='valeria',
                name='Should not persist',
                booking_url='',
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(
            (
                self.profile.name,
                self.profile.booking_url,
                self.profile.is_active,
                self.profile.order,
            ),
            before,
        )
        self.assertContains(response, 'value="Should not persist"')

    def test_delete_is_post_only_and_removes_unused_profile(self):
        unused = CallHost.objects.create(
            name='Unused', slug='unused', is_active=False,
        )
        delete_url = f'/studio/call-hosts/{unused.pk}/delete'
        self.assertEqual(self.client.get(delete_url).status_code, 405)
        response = self.client.post(delete_url, follow=True)
        self.assertRedirects(response, '/studio/call-hosts/')
        self.assertFalse(CallHost.objects.filter(pk=unused.pk).exists())
        self.assertContains(response, 'Call profile “Unused” deleted.')

    def test_delete_refuses_even_canceled_booked_call_history(self):
        booked_call = BookedCall.objects.create(
            host=self.profile,
            invitee_email='history@example.com',
            status=STATUS_CANCELED,
            calendly_event_uri='https://api.calendly.com/scheduled_events/history',
        )
        response = self.client.post(
            f'/studio/call-hosts/{self.profile.pk}/delete',
            follow=True,
        )
        self.assertRedirects(
            response,
            f'/studio/call-hosts/{self.profile.pk}/edit',
        )
        self.assertContains(response, 'booked-call history')
        self.assertTrue(CallHost.objects.filter(pk=self.profile.pk).exists())
        self.assertTrue(BookedCall.objects.filter(pk=booked_call.pk).exists())
        with self.assertRaises(ProtectedError):
            self.profile.delete()
