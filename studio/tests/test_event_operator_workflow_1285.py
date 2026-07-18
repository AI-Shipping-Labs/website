"""Studio event-operator workflow coverage for issue #1285."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, tag
from django.urls import reverse
from django.utils import timezone

from content.models import Workshop
from email_app.models import EmailCampaign
from events.models import (
    Event,
    EventFeedback,
    EventHost,
    EventRegistration,
    EventSeries,
    Host,
)

User = get_user_model()


class EventOperatorBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='operator-1285@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='pw')

    def make_event(self, slug='operator-event', **overrides):
        values = {
            'title': 'Operator Event',
            'slug': slug,
            'start_datetime': timezone.now() + timedelta(days=7),
            'end_datetime': timezone.now() + timedelta(days=7, hours=2),
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'origin': 'studio',
        }
        values.update(overrides)
        return Event.objects.create(**values)


@tag('core')
class CampaignEventShortcutTest(EventOperatorBase):
    def test_generic_event_prefill_is_blank_and_uses_real_recipient_count(self):
        event = self.make_event()
        eligible = User.objects.create_user(
            email='eligible-1285@test.com', password='pw', email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=eligible)

        before = EmailCampaign.objects.count()
        response = self.client.get(
            reverse('studio_campaign_create'), {'event': event.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailCampaign.objects.count(), before)
        self.assertEqual(response.context['selected_event_id'], event.pk)
        self.assertEqual(response.context['recipient_count'], 1)
        self.assertEqual(response.context['campaign'].subject, '')
        self.assertEqual(response.context['campaign'].body, '')
        self.assertContains(response, 'data-testid="recipient-count-helper"')

    def test_unknown_or_malformed_event_falls_back_without_disclosure(self):
        for value in ('bad', '999999999'):
            with self.subTest(value=value):
                response = self.client.get(
                    reverse('studio_campaign_create'), {'event': value},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context['selected_event_id'])
                self.assertIsNone(response.context['campaign'])

    def test_recording_template_and_operator_datetime_options_are_preserved(self):
        first = self.make_event(slug='office-hours-one', title='Office Hours')
        second = self.make_event(
            slug='office-hours-two',
            title='Office Hours',
            start_datetime=first.start_datetime + timedelta(days=1),
            end_datetime=first.end_datetime + timedelta(days=1),
        )
        response = self.client.get(reverse('studio_campaign_create'), {
            'event': first.pk,
            'template': 'recording_available',
        })
        self.assertEqual(response.context['selected_event_id'], first.pk)
        self.assertTrue(response.context['campaign'].subject)
        self.assertContains(response, 'Office Hours — ')
        self.assertContains(response, f'value="{first.pk}"')
        self.assertContains(response, f'value="{second.pk}"')

    def test_event_edit_links_to_blank_registrant_campaign(self):
        event = self.make_event()
        response = self.client.get(
            reverse('studio_event_edit', args=[event.pk]),
        )
        self.assertContains(response, 'data-testid="new-campaign-to-registrants"')
        self.assertContains(
            response,
            f'/studio/campaigns/new?event={event.pk}',
        )
        self.assertContains(response, 'Email registrants: recording available')

    def test_event_selection_preview_uses_same_audience_logic_without_writes(self):
        event = self.make_event(slug='preview-event')
        eligible = User.objects.create_user(
            email='preview-eligible-1285@test.com',
            password='pw',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=eligible)
        url = reverse('studio_campaign_recipient_count')
        before = EmailCampaign.objects.count()

        response = self.client.get(url, {'event': event.pk})
        self.assertEqual(response.json(), {
            'selected_event_id': event.pk,
            'recipient_count': 1,
        })
        fallback = self.client.get(url, {'event': 'not-an-id'})
        self.assertEqual(fallback.status_code, 200)
        self.assertIsNone(fallback.json()['selected_event_id'])
        self.assertEqual(EmailCampaign.objects.count(), before)


@tag('core')
class EventDuplicateWorkflowTest(EventOperatorBase):
    def setUp(self):
        super().setUp()
        self.host_one = Host.objects.create(name='First Host', slug='first-host')
        self.host_two = Host.objects.create(name='Second Host', slug='second-host')
        self.source = self.make_event(
            title='Repeat Me',
            slug='repeat-me',
            description='Safe description',
            platform='custom',
            location='Community room',
            tags=['agents', 'shipping'],
            required_level=20,
            external_host='Maven',
            zoom_join_url='https://secret.example/join',
            zoom_meeting_id='secret-meeting',
            host_email='host@example.com',
            status='upcoming',
        )
        EventHost.objects.create(event=self.source, host=self.host_two, position=0)
        EventHost.objects.create(event=self.source, host=self.host_one, position=1)

    def test_duplicate_get_is_read_only_and_copies_only_safe_prefill(self):
        before = Event.objects.count()
        response = self.client.get(
            reverse('studio_event_duplicate', args=[self.source.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), before)
        values = response.context['form_values']
        self.assertEqual(values['title'], 'Repeat Me (copy)')
        self.assertEqual(values['description'], 'Safe description')
        self.assertEqual(values['event_date'], '')
        self.assertEqual(values['event_time'], '')
        self.assertEqual(values['slug'], '')
        self.assertEqual(values['custom_url'], '')
        self.assertEqual(values['host_email'], '')
        self.assertEqual(
            response.context['selected_host_ids'],
            [self.host_two.pk, self.host_one.pk],
        )
        self.assertContains(response, 'data-testid="event-duplicate-context"')
        self.assertContains(response, 'Duplicating “Repeat Me”')

    def test_duplicate_route_rejects_mutation_and_is_staff_gated(self):
        url = reverse('studio_event_duplicate', args=[self.source.pk])
        self.assertEqual(self.client.post(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    @patch('studio.views.events.enqueue_if_missing')
    def test_invalid_then_valid_duplicate_creates_one_sanitized_draft(self, enqueue):
        create_url = reverse('studio_event_new')
        base = {
            'duplicate_source_id': str(self.source.pk),
            'title': 'Repeat Me (copy)',
            'slug': 'forged-slug',
            'description': 'Safe description',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'platform': 'custom',
            'status': 'upcoming',
            'required_level': '20',
            'location': 'Community room',
            'tags': 'agents, shipping',
            'external_host': 'Maven',
            'custom_url': 'https://forged.example/join',
            'host_email': 'forged@example.com',
            'host_ids': [str(self.host_two.pk), str(self.host_one.pk)],
        }
        before = Event.objects.count()
        invalid = self.client.post(create_url, base)
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(invalid.context['form_values']['title'], 'Repeat Me (copy)')

        scheduled = date.today() + timedelta(days=14)
        response = self.client.post(create_url, {
            **base,
            'event_date': scheduled.strftime('%d/%m/%Y'),
            'event_time': '18:30',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), before + 1)
        duplicate = Event.objects.exclude(pk=self.source.pk).get()
        self.assertEqual(duplicate.slug, 'repeat-me-copy')
        self.assertEqual(duplicate.status, 'draft')
        self.assertEqual(duplicate.kind, 'standard')
        self.assertEqual(duplicate.origin, 'studio')
        self.assertTrue(duplicate.published)
        self.assertEqual(duplicate.zoom_join_url, '')
        self.assertEqual(duplicate.zoom_meeting_id, '')
        self.assertEqual(duplicate.host_email, '')
        self.assertIsNone(duplicate.event_series_id)
        self.assertEqual(
            [host.pk for host in duplicate.ordered_hosts],
            [self.host_two.pk, self.host_one.pk],
        )
        self.assertContains(response, 'Event “Repeat Me (copy)” created.')
        enqueue.assert_called_once_with('event', duplicate.pk)


@tag('core')
class SeriesBulkPublishWorkflowTest(EventOperatorBase):
    def setUp(self):
        super().setUp()
        self.series = EventSeries.objects.create(
            name='Publish Series', slug='publish-series', cadence='weekly',
            day_of_week=1, start_time=time(18), timezone='UTC',
        )
        self.other_series = EventSeries.objects.create(
            name='Other Series', slug='other-series-1285', cadence='weekly',
            day_of_week=2, start_time=time(18), timezone='UTC',
        )
        self.drafts = [
            self.make_event(
                slug=f'publish-draft-{index}', event_series=self.series,
                series_position=index,
            )
            for index in (1, 2)
        ]
        self.cancelled = self.make_event(
            slug='publish-cancelled', event_series=self.series,
            status='cancelled', series_position=3,
        )
        self.other = self.make_event(
            slug='publish-other', event_series=self.other_series,
        )

    def test_detail_shows_exact_form_owned_plural_confirmation(self):
        response = self.client.get(
            reverse('studio_event_series_detail', args=[self.series.pk]),
        )
        self.assertEqual(response.context['draft_count'], 2)
        self.assertContains(response, 'Publish all drafts (2)')
        self.assertContains(
            response,
            'Publish 2 draft occurrences in “Publish Series”?',
        )
        self.assertContains(
            response, 'data-testid="event-series-publish-all-form"',
        )

    @patch('events.services.occurrence_publication.run_occurrence_publication_lifecycle')
    def test_publish_all_is_scoped_atomic_and_retry_safe(self, lifecycle):
        url = reverse('studio_event_series_publish_all', args=[self.series.pk])
        with self.assertLogs(
            'events.services.occurrence_publication', level='INFO',
        ) as first_log:
            response = self.client.post(url, follow=True)
        self.assertContains(response, 'Published 2 draft occurrences.')
        self.assertIn('selected=2 changed=2', first_log.output[0])
        self.assertEqual(lifecycle.call_count, 2)
        self.assertSetEqual(
            set(self.series.events.filter(status='upcoming').values_list('pk', flat=True)),
            {event.pk for event in self.drafts},
        )
        self.cancelled.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.cancelled.status, 'cancelled')
        self.assertEqual(self.other.status, 'draft')

        with self.assertLogs(
            'events.services.occurrence_publication', level='INFO',
        ) as replay_log:
            replay = self.client.post(url, follow=True)
        self.assertContains(replay, 'No draft occurrences to publish.')
        self.assertIn('selected=0 changed=0', replay_log.output[0])
        self.assertEqual(lifecycle.call_count, 2)
        self.assertNotContains(replay, 'Publish all drafts')

    def test_bulk_publish_is_post_only_staff_only_and_csrf_protected(self):
        url = reverse('studio_event_series_publish_all', args=[self.series.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.staff.email, password='pw')
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertEqual(self.series.events.filter(status='draft').count(), 2)


@tag('core')
class SeriesBulkPublishConcurrencyTest(TransactionTestCase):
    def test_two_real_transactions_claim_one_draft_and_run_lifecycle_once(self):
        from events.services import occurrence_publication

        series = EventSeries.objects.create(
            name='Concurrent Publish', slug='concurrent-publish-1285',
            start_time=time(18), timezone='UTC',
        )
        event = Event.objects.create(
            title='One contested draft', slug='contested-draft-1285',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft', origin='studio', event_series=series,
        )
        barrier = threading.Barrier(2)

        def publish(actor):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return occurrence_publication.publish_series_drafts(
                    series.pk, actor_label=actor,
                )
            finally:
                close_old_connections()

        with (
            patch.object(
                occurrence_publication,
                'run_occurrence_publication_lifecycle',
            ) as lifecycle,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(publish, ('race-a', 'race-b')))

        event.refresh_from_db()
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(
            sorted(result.published_count for result in results),
            [0, 1],
        )
        self.assertEqual(lifecycle.call_count, 1)


@tag('core')
class EventDeleteWorkflowTest(EventOperatorBase):
    def test_delete_visibility_matches_server_authority(self):
        eligible = self.make_event(slug='delete-visible')
        synced = self.make_event(
            slug='delete-synced', origin='github', source_repo='content',
        )
        registered = self.make_event(slug='delete-registered')
        member = User.objects.create_user(email='registered-1285@test.com')
        EventRegistration.objects.create(event=registered, user=member)
        targeted = self.make_event(slug='delete-targeted')
        EmailCampaign.objects.create(
            subject='Targeted', body='Body', target_event=targeted,
        )

        visible = self.client.get(reverse('studio_event_edit', args=[eligible.pk]))
        self.assertContains(visible, 'data-testid="studio-event-delete-submit"')
        self.assertContains(visible, 'Delete “Operator Event”? This cannot be undone.')
        self.assertContains(visible, 'Linked operational history is removed.')
        for protected in (synced, registered, targeted):
            response = self.client.get(
                reverse('studio_event_edit', args=[protected.pk]),
            )
            self.assertNotContains(response, 'data-testid="studio-event-delete-submit"')
            self.assertContains(response, 'data-testid="studio-event-duplicate"')

    def test_forged_refusals_preserve_rows_and_targeting(self):
        scenarios = []
        synced = self.make_event(
            slug='refuse-synced', origin='github', source_repo='content',
        )
        scenarios.append((synced, 'Source-managed events cannot be deleted.'))
        registered = self.make_event(slug='refuse-registered')
        member = User.objects.create_user(email='refusal-member@test.com')
        registration = EventRegistration.objects.create(
            event=registered, user=member,
        )
        scenarios.append((registered, 'Events with registrations cannot be deleted.'))
        targeted = self.make_event(slug='refuse-targeted')
        campaign = EmailCampaign.objects.create(
            subject='Keep scope', body='Body', target_event=targeted,
        )
        scenarios.append((targeted, 'Campaigns targeting this event must be retargeted first.'))

        for event, message in scenarios:
            with self.subTest(event=event.slug):
                with self.assertLogs('studio.views.events', level='WARNING') as log:
                    response = self.client.post(
                        reverse('studio_event_delete', args=[event.pk]),
                        follow=True,
                    )
                self.assertContains(response, message)
                self.assertTrue(Event.objects.filter(pk=event.pk).exists())
                self.assertIn(f'event_id={event.pk}', log.output[0])
        self.assertTrue(EventRegistration.objects.filter(pk=registration.pk).exists())
        campaign.refresh_from_db()
        self.assertEqual(campaign.target_event_id, targeted.pk)

    def test_eligible_delete_cascades_history_unlinks_workshop_and_replay_404s(self):
        event = self.make_event(slug='delete-once')
        member = User.objects.create_user(email='feedback-1285@test.com')
        EventFeedback.objects.create(event=event, user=member, rating=5)
        workshop = Workshop.objects.create(
            title='Linked workshop', slug='linked-workshop-1285',
            description='Description', date=date.today(), event=event,
        )
        url = reverse('studio_event_delete', args=[event.pk])
        with self.assertLogs('studio.views.events', level='INFO') as accepted:
            response = self.client.post(url, follow=True)
        self.assertContains(response, 'Event “Operator Event” deleted.')
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())
        self.assertFalse(EventFeedback.objects.filter(event_id=event.pk).exists())
        workshop.refresh_from_db()
        self.assertIsNone(workshop.event_id)
        self.assertIn("'events.eventfeedback': 1", accepted.output[0])

        with self.assertLogs('studio.views.events', level='WARNING') as replay:
            second = self.client.post(url)
        self.assertEqual(second.status_code, 404)
        self.assertIn('reason=not_found', replay.output[0])

    def test_delete_is_post_only_and_csrf_protected(self):
        event = self.make_event(slug='csrf-delete')
        url = reverse('studio_event_delete', args=[event.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.staff.email, password='pw')
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_delete_rechecks_registration_added_after_eligible_render(self):
        event = self.make_event(slug='delete-race-recheck')
        edit_url = reverse('studio_event_edit', args=[event.pk])
        delete_url = reverse('studio_event_delete', args=[event.pk])
        eligible_page = self.client.get(edit_url)
        self.assertContains(
            eligible_page, 'data-testid="studio-event-delete-submit"',
        )

        member = User.objects.create_user(email='late-race-1285@test.com')
        registration = EventRegistration.objects.create(
            event=event, user=member,
        )
        with self.assertLogs('studio.views.events', level='WARNING') as log:
            refused = self.client.post(delete_url, follow=True)

        self.assertContains(
            refused, 'Events with registrations cannot be deleted.',
        )
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())
        self.assertTrue(
            EventRegistration.objects.filter(pk=registration.pk).exists(),
        )
        self.assertIn('reason=has_registrations', log.output[0])


@tag('core')
class EventCreateFeedbackTest(EventOperatorBase):
    @patch('studio.views.events.enqueue_if_missing')
    def test_future_and_past_create_messages_are_exact_and_non_blocking(self, _enqueue):
        def payload(title, day):
            return {
                'title': title,
                'slug': '',
                'description': '',
                'event_date': day.strftime('%d/%m/%Y'),
                'event_time': '12:00',
                'duration_hours': '1',
                'timezone': 'UTC',
                'platform': 'zoom',
                'status': 'draft',
                'required_level': '0',
                'location': '',
                'tags': '',
                'external_host': '',
                'custom_url': '',
                'host_email': '',
            }

        future = self.client.post(
            reverse('studio_event_new'),
            payload('Future & Safe', date.today() + timedelta(days=7)),
            follow=True,
        )
        self.assertContains(future, 'Event “Future &amp; Safe” created.', html=False)
        self.assertNotContains(
            future, "This event&#x27;s start time is in the past."
        )

        past = self.client.post(
            reverse('studio_event_new'),
            payload('Past event', date.today() - timedelta(days=7)),
            follow=True,
        )
        self.assertContains(past, 'Event “Past event” created.')
        self.assertContains(
            past, "This event&#x27;s start time is in the past."
        )
        self.assertEqual(Event.objects.get(slug='past-event').status, 'draft')

    @patch('studio.views.event_series.enqueue_if_missing')
    def test_series_create_flashes_success_and_keeps_occurrences_draft(self, _enqueue):
        start = date.today() + timedelta(days=14)
        response = self.client.post(reverse('studio_event_series_new'), {
            'name': 'Deliberate Series',
            'slug': '',
            'description': '',
            'start_date': start.strftime('%d/%m/%Y'),
            'start_time': '18:00',
            'duration_hours': '1',
            'occurrences': '3',
            'timezone': 'UTC',
            'required_level': '0',
            'kind': 'standard',
            'platform': 'zoom',
        }, follow=True)
        self.assertContains(response, 'Event series “Deliberate Series” created.')
        self.assertContains(response, 'Publish all drafts (3)')
        series = EventSeries.objects.get(slug='deliberate-series')
        self.assertEqual(series.events.filter(status='draft').count(), 3)
