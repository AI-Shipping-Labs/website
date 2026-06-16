"""Tests for event hosts (#994)."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from email_app.models import EmailLog
from events.models import Event, EventHost, Host
from events.services.host_invite import maybe_send_initial_host_invite


@tag('core')
class HostModelTest(TestCase):
    def test_bio_markdown_renders_to_html(self):
        host = Host.objects.create(
            name='Markdown Host',
            slug='markdown-host',
            bio='**bold** and a [link](https://example.com)',
        )

        self.assertIn('<strong>bold</strong>', host.bio_html)
        self.assertIn('href="https://example.com"', host.bio_html)

    def test_blank_bio_renders_blank_html(self):
        host = Host.objects.create(name='Blank Host', slug='blank-host', bio='')
        self.assertEqual(host.bio_html, '')

    def test_static_photo_fallback_for_seed_hosts(self):
        alexey = Host.objects.get(slug='alexey-grigorev')
        valeriia = Host.objects.get(slug='valeriia-kuka')

        self.assertTrue(alexey.display_photo_url.endswith('alexey.png'))
        self.assertTrue(valeriia.display_photo_url.endswith('valeriia.png'))

    def test_ordered_hosts_returns_eventhost_position_order(self):
        event = Event.objects.create(
            title='Ordered Host Event',
            slug='ordered-host-event',
            start_datetime=timezone.now() + timedelta(days=3),
            status='upcoming',
        )
        first = Host.objects.create(name='First Host', slug='first-host')
        second = Host.objects.create(name='Second Host', slug='second-host')
        EventHost.objects.create(event=event, host=second, position=1)
        EventHost.objects.create(event=event, host=first, position=0)

        self.assertEqual(event.ordered_hosts, [first, second])


@tag('core')
class SeededHostTest(TestCase):
    def test_seeded_founders_have_about_page_identity(self):
        alexey = Host.objects.get(slug='alexey-grigorev')
        valeriia = Host.objects.get(slug='valeriia-kuka')

        self.assertEqual(alexey.name, 'Alexey Grigorev')
        self.assertEqual(alexey.email, 'alexey@aishippinglabs.com')
        self.assertIn('DataTalks.Club', alexey.bio)
        self.assertTrue(alexey.bio_html)
        self.assertTrue(alexey.display_photo_url.endswith('alexey.png'))

        self.assertEqual(valeriia.name, 'Valeriia Kuka')
        self.assertEqual(valeriia.email, 'valeriia@aishippinglabs.com')
        self.assertIn('Content strategist', valeriia.bio)
        self.assertTrue(valeriia.bio_html)
        self.assertTrue(valeriia.display_photo_url.endswith('valeriia.png'))


@tag('core')
class EventHostsDetailTest(TestCase):
    def test_detail_renders_hosts_in_order_with_bio_html_and_photos(self):
        event = Event.objects.create(
            title='Public Hosted Event',
            slug='public-hosted-event',
            start_datetime=timezone.now() + timedelta(days=3),
            status='upcoming',
        )
        alexey = Host.objects.get(slug='alexey-grigorev')
        valeriia = Host.objects.get(slug='valeriia-kuka')
        valeriia.bio = '**Host bio** for the event.'
        valeriia.save()
        EventHost.objects.create(event=event, host=valeriia, position=0)
        EventHost.objects.create(event=event, host=alexey, position=1)

        response = self.client.get(event.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-hosts"')
        body = response.content.decode()
        hosts_section = body[body.index('data-testid="event-hosts"'):]
        self.assertLess(
            hosts_section.index('Valeriia Kuka'),
            hosts_section.index('Alexey Grigorev'),
        )
        self.assertIn('<strong>Host bio</strong>', body)
        self.assertIn('valeriia.png', body)
        self.assertIn('alexey.png', body)

    def test_detail_without_hosts_has_no_empty_hosts_section(self):
        event = Event.objects.create(
            title='No Hosts Event',
            slug='no-hosts-event',
            start_datetime=timezone.now() + timedelta(days=3),
            status='upcoming',
        )

        response = self.client.get(event.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-hosts"')
        self.assertNotContains(response, 'Hosted by')


@override_settings(EVENTS_HOST_INVITE_EMAIL='alexey.s.grigoriev@gmail.com')
@tag('core')
class HostEmailInviteGuardTest(TestCase):
    def test_host_email_is_display_only_not_calendar_invite_recipient(self):
        event = Event.objects.create(
            title='Invite Guard Event',
            slug='invite-guard-event',
            start_datetime=timezone.now() + timedelta(days=3),
            end_datetime=timezone.now() + timedelta(days=3, hours=1),
            status='upcoming',
            host_email='',
        )
        host = Host.objects.create(
            name='Valeriia Display',
            slug='valeriia-display',
            email='valeriia@aishippinglabs.com',
        )
        EventHost.objects.create(event=event, host=host, position=0)

        with patch(
            'events.services.host_invite._send_raw_email',
            return_value='ses-1',
        ) as mock_send:
            log = maybe_send_initial_host_invite(event)

        self.assertIsNotNone(log)
        self.assertEqual(
            mock_send.call_args.kwargs['to_email'],
            'alexey.s.grigoriev@gmail.com',
        )
        self.assertNotEqual(
            mock_send.call_args.kwargs['to_email'],
            'valeriia@aishippinglabs.com',
        )
        self.assertTrue(
            EmailLog.objects.filter(
                event=event,
                recipient_email='alexey.s.grigoriev@gmail.com',
            ).exists(),
        )
