"""Tests for Studio event-host management (#994)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from events.models import Event, EventHost, Host
from tests.fixtures import StaffUserMixin

User = get_user_model()


@tag('core')
class StudioHostAccessTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='member-hosts@test.com',
            password='pw',
        )

    def test_list_requires_login_anonymous_redirected(self):
        response = self.client.get('/studio/hosts/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_list_forbidden_for_non_staff(self):
        self.client.force_login(self.member)
        response = self.client.get('/studio/hosts/')
        self.assertEqual(response.status_code, 403)


@tag('core')
class StudioHostCrudTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_shows_seeded_hosts_and_create_link(self):
        response = self.client.get('/studio/hosts/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/hosts/list.html')
        self.assertContains(response, 'Alexey Grigorev')
        self.assertContains(response, 'Valeriia Kuka')
        self.assertContains(response, 'href="/studio/hosts/new"')

    def test_create_host_saves_markdown_bio_and_email(self):
        response = self.client.post(
            '/studio/hosts/new',
            {
                'name': 'Jordan Lee',
                'slug': 'jordan-lee',
                'bio': '**Builder** bio',
                'photo_url': 'https://cdn.example.com/jordan.jpg',
                'email': 'jordan@example.com',
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        host = Host.objects.get(slug='jordan-lee')
        self.assertEqual(host.name, 'Jordan Lee')
        self.assertEqual(host.email, 'jordan@example.com')
        self.assertIn('<strong>Builder</strong>', host.bio_html)

        list_response = self.client.get('/studio/hosts/')
        self.assertContains(list_response, 'Jordan Lee')

    def test_edit_host_updates_active_state(self):
        host = Host.objects.get(slug='alexey-grigorev')

        response = self.client.post(
            f'/studio/hosts/{host.pk}/edit',
            {
                'name': 'Alexey Grigorev',
                'slug': 'alexey-grigorev',
                'bio': 'Updated bio',
                'photo_url': '',
                'email': 'alexey@aishippinglabs.com',
                # is_active omitted -> unchecked
            },
        )

        self.assertEqual(response.status_code, 302)
        host.refresh_from_db()
        self.assertEqual(host.bio, 'Updated bio')
        self.assertFalse(host.is_active)

    def test_new_host_is_available_on_event_edit_form(self):
        host = Host.objects.create(
            name='Jordan Lee',
            slug='jordan-edit-option',
            email='jordan@example.com',
        )
        event = Event.objects.create(
            title='Editable Event',
            slug='editable-event',
            start_datetime=timezone.now() + timedelta(days=3),
            status='upcoming',
        )

        response = self.client.get(f'/studio/events/{event.pk}/edit')

        self.assertContains(response, 'data-testid="studio-event-hosts"')
        self.assertContains(response, f'value="{host.id}"')
        self.assertContains(response, 'Jordan Lee')


@tag('core')
class StudioEventHostAssignmentTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.alexey = Host.objects.get(slug='alexey-grigorev')
        self.valeriia = Host.objects.get(slug='valeriia-kuka')

    def test_create_event_persists_selected_hosts(self):
        response = self.client.post(
            '/studio/events/new',
            {
                'title': 'Studio Hosted Create',
                'slug': 'studio-hosted-create',
                'description': 'Details',
                'event_date': '20/06/2026',
                'event_time': '17:00',
                'duration_hours': '1',
                'timezone': 'UTC',
                'platform': 'zoom',
                'status': 'upcoming',
                'required_level': '0',
                'location': 'Zoom',
                'tags': '',
                'external_host': '',
                'custom_url': '',
                'host_email': '',
                'host_ids': [str(self.valeriia.id), str(self.alexey.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(slug='studio-hosted-create')
        self.assertEqual(event.ordered_hosts, [self.valeriia, self.alexey])

    def test_edit_event_preselects_and_updates_hosts(self):
        event = Event.objects.create(
            title='Studio Hosted Edit',
            slug='studio-hosted-edit',
            start_datetime=timezone.now() + timedelta(days=3),
            end_datetime=timezone.now() + timedelta(days=3, hours=1),
            timezone='UTC',
            status='upcoming',
        )
        EventHost.objects.create(event=event, host=self.alexey, position=0)

        get_response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(get_response, 'data-testid="studio-event-hosts"')
        self.assertIn(self.alexey.id, get_response.context['selected_host_ids'])

        response = self.client.post(
            f'/studio/events/{event.pk}/edit',
            {
                'title': event.title,
                'slug': event.slug,
                'description': event.description,
                'event_date': event.start_datetime.strftime('%d/%m/%Y'),
                'event_time': event.start_datetime.strftime('%H:%M'),
                'duration_hours': '1',
                'timezone': 'UTC',
                'platform': 'zoom',
                'status': 'upcoming',
                'required_level': '0',
                'location': event.location,
                'tags': '',
                'external_host': '',
                'custom_url': '',
                'host_email': '',
                'post_event_summary': '',
                'host_ids': [str(self.valeriia.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.ordered_hosts, [self.valeriia])
