"""The recap-ready notice goes out automatically, once, when a recap is ready.

Saving a recap onto a finished event (Studio, API, recap draft) queues the
django-q job; the job announces the recap to the whole audience (registrants
and linked cohort members) and writes a per-event guard. Editing the recap
later never queues or sends again. The manual command stays a fallback.
"""

import datetime
from datetime import timedelta
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course
from events.models import Event, EventRegistration, EventSeries
from events.services.event_audience import resolve_event_audience
from events.services.event_recap_notification import notify_recap_ready
from events.tasks.complete_finished_events import complete_finished_events
from events.tasks.notify_recap_auto import send_recap_auto_notify
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

ENQUEUE = 'events.services.event_recap_notification.enqueue_recap_auto_notify'


@tag('core')
class RecapAutoNotifyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.series = EventSeries.objects.create(name='Office hours', slug='office-hours-auto')
        cls.event = Event.objects.create(
            title='Office hours: week 2', slug='office-hours-week-2-auto',
            start_datetime=now - timedelta(hours=3), end_datetime=now - timedelta(hours=1),
            status='completed', published=True, event_series=cls.series,
        )
        course = Course.objects.create(title='Buildcamp', slug='buildcamp-auto', status='published')
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 12, 1),
            event_series=cls.series,
        )
        cls.member = User.objects.create_user(email='auto-member@test.com', email_verified=True)
        cls.registrant = User.objects.create_user(email='auto-registrant@test.com', email_verified=True)
        CohortEnrollment.objects.create(cohort=cohort, user=cls.member)
        EventRegistration.objects.create(event=cls.event, user=cls.registrant)

    def _save_recap(self, notes):
        event = Event.objects.get(pk=self.event.pk)
        event.recap_notes = notes
        with self.captureOnCommitCallbacks(execute=True):
            event.save()
        return event

    def _recap_recipients(self):
        return sorted(EmailDelivery.objects.filter(
            purpose='event_recap_ready',
        ).values_list('recipient_email', flat=True))

    def test_recap_becoming_ready_announces_once_and_edits_never_resend(self):
        with patch(ENQUEUE) as enqueue:
            self._save_recap('## What we covered\n\nEvals.')
            enqueue.assert_called_once_with(self.event.pk)

            first = send_recap_auto_notify(self.event.pk)
            self._save_recap('## What we covered\n\nEvals, edited.')
            second = send_recap_auto_notify(self.event.pk)

        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(first['emailed'], 2)
        self.assertEqual(second, {
            'status': 'skipped', 'reason': 'already_announced', 'event_id': self.event.pk,
        })
        self.assertEqual(self._recap_recipients(), [
            'auto-member@test.com', 'auto-registrant@test.com',
        ])

    def test_recap_already_sent_manually_is_not_announced_again(self):
        with patch(ENQUEUE):
            self._save_recap('Notes.')
        notify_recap_ready(Event.objects.get(pk=self.event.pk))
        with patch(ENQUEUE) as enqueue:
            self._save_recap('')
            self._save_recap('Rewritten notes.')
        enqueue.assert_not_called()
        self.assertEqual(len(self._recap_recipients()), 2)

    def test_toggle_off_does_not_queue(self):
        IntegrationSetting.objects.create(key='RECAP_AUTO_NOTIFY', value='false', group='site')
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        with patch(ENQUEUE) as enqueue:
            self._save_recap('Notes.')
        enqueue.assert_not_called()

    def test_creating_a_finished_event_with_a_recap_does_not_queue(self):
        now = timezone.now()
        with patch(ENQUEUE) as enqueue, self.captureOnCommitCallbacks(execute=True):
            Event.objects.create(
                title='Imported', slug='imported-with-recap',
                start_datetime=now - timedelta(days=30), status='completed',
                event_series=self.series, recap_notes='Old recap.',
            )
        enqueue.assert_not_called()

    def test_recap_written_before_the_end_is_announced_when_the_event_completes(self):
        now = timezone.now()
        Event.objects.filter(pk=self.event.pk).update(
            status='upcoming', end_datetime=now + timedelta(hours=1),
        )
        with patch(ENQUEUE) as enqueue:
            self._save_recap('Pre-written recap.')
            enqueue.assert_not_called()
            Event.objects.filter(pk=self.event.pk).update(end_datetime=now - timedelta(minutes=1))
            with self.captureOnCommitCallbacks(execute=True):
                complete_finished_events()
        enqueue.assert_called_once_with(self.event.pk)


@tag('core')
class EventAudienceResolverTest(TestCase):
    """The shared resolver: union of sources, one entry per user, dated cohorts only."""

    def test_union_dedupes_and_skips_self_paced_other_series_and_inactive(self):
        series = EventSeries.objects.create(name='S', slug='audience-s')
        other = EventSeries.objects.create(name='O', slug='audience-o')
        event = Event.objects.create(
            title='E', slug='audience-e', start_datetime=timezone.now(), event_series=series,
        )
        course = Course.objects.create(title='C', slug='audience-c', status='published')
        dated = Cohort.objects.create(
            course=course, name='Cohort 4', event_series=series,
            start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 12, 1),
        )
        self_paced = Cohort.objects.create(
            course=course, name='Self-paced', mode='self_paced', event_series=series,
        )
        other_cohort = Cohort.objects.create(
            course=course, name='Cohort 3', event_series=other,
            start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 8, 1),
        )
        users = {
            name: User.objects.create_user(email=f'{name}@aud.test', is_active=name != 'inactive')
            for name in ('registrant', 'both', 'member', 'self-paced', 'other', 'inactive')
        }
        EventRegistration.objects.create(event=event, user=users['registrant'])
        EventRegistration.objects.create(event=event, user=users['both'])
        for name in ('both', 'member', 'inactive'):
            CohortEnrollment.objects.create(cohort=dated, user=users[name])
        CohortEnrollment.objects.create(cohort=self_paced, user=users['self-paced'])
        CohortEnrollment.objects.create(cohort=other_cohort, user=users['other'])

        audience = resolve_event_audience(event, include_book_club=False)

        by_email = {
            member.user.email: sorted(reason.source for reason in member.reasons)
            for member in audience
        }
        self.assertEqual(by_email, {
            'registrant@aud.test': ['registered'],
            'both@aud.test': ['cohort', 'registered'],
            'member@aud.test': ['cohort'],
        })
        registrants_only = resolve_event_audience(
            event, include_cohort=False, include_book_club=False,
        )
        self.assertEqual(
            {member.user.email for member in registrants_only},
            {'registrant@aud.test', 'both@aud.test'},
        )
