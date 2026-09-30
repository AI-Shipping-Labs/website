"""Pre-event reminders reach the cohort linked to the event's series.

The 24h/20m reminders go to the shared event audience: registrants plus
members of every dated cohort whose ``event_series`` is the event's series.
This covers the Cohort 4 office-hours case: a hidden series and an
unpublished occurrence, members who never registered, links to the course
session unit, one email for a registrant who is also in the cohort, and the
complaint/bounce/unsubscribe policy.
"""

import datetime
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from email_app.models import EmailLog
from email_app.testing import StubSESClient, deliver_pending_mail
from events.models import Event, EventRegistration, EventSeries
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.models import EventReminderLog, Notification
from notifications.services.event_reminders import check_event_reminders

User = get_user_model()


@tag('core')
class CohortEventRemindersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        series = EventSeries.objects.create(
            name='Buildcamp office hours cohort 4',
            slug='buildcamp-office-hours-cohort-4',
            visibility='hidden',
        )
        cls.event = Event.objects.create(
            title='Office hours: week 3', slug='office-hours-week-3',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='upcoming', published=False,
            event_series=series, series_position=3,
        )
        course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-reminders', status='published',
        )
        module = Module.objects.create(course=course, title='Week 3', slug='week-3', sort_order=1)
        cls.unit = Unit.objects.create(
            module=module, title='Office hours 3', slug='office-hours-3',
            kind='event', sort_order=1, session_position=3,
        )
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4', external_key='4',
            start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 12, 1),
            event_series=series,
        )
        self_paced = Cohort.objects.create(
            course=course, name='Self-paced', mode='self_paced', event_series=series,
        )

        def user(name, **kwargs):
            return User.objects.create_user(
                email=f'{name}@test.com', email_verified=True, **kwargs,
            )

        cls.registrant = user('registrant')
        cls.member = user('member', unsubscribed=True)
        cls.both = user('both')
        cls.bounced = user('bounced', bounce_state=User.BounceState.PERMANENT)
        cls.complained = user('complained')
        cls.self_paced_member = user('self-paced')
        user('outsider')

        EventRegistration.objects.create(event=cls.event, user=cls.registrant)
        EventRegistration.objects.create(event=cls.event, user=cls.both)
        for member in (cls.member, cls.both, cls.bounced, cls.complained):
            CohortEnrollment.objects.create(cohort=cohort, user=member)
        CohortEnrollment.objects.create(cohort=self_paced, user=cls.self_paced_member)
        EmailLog.objects.create(
            user=cls.complained, recipient_email=cls.complained.email,
            email_type='event_registration', complained_at=timezone.now(),
        )
        cls.unit_path = f'{cls.unit.get_absolute_url()}?cohort=4'

    def _reminded_ids(self):
        return set(EventReminderLog.objects.filter(
            event=self.event, interval='24h',
        ).values_list('user_id', flat=True))

    def _emailed(self):
        return sorted(EmailDelivery.objects.filter(
            purpose='event_reminder',
        ).values_list('recipient_email', flat=True))

    def test_hidden_unpublished_event_reminds_registrants_and_cohort_once(self):
        check_event_reminders()
        check_event_reminders()

        self.assertEqual(self._reminded_ids(), {
            self.registrant.pk, self.member.pk, self.both.pk,
            self.bounced.pk, self.complained.pk,
        })
        # One email each; a newsletter unsubscribe does not suppress a cohort
        # member (transactional), a permanent bounce or complaint does.
        self.assertEqual(self._emailed(), [
            'both@test.com', 'member@test.com', 'registrant@test.com',
        ])
        # The suppressed members still get the in-app bell.
        self.assertTrue(Notification.objects.filter(
            user=self.bounced, notification_type='event_reminder',
        ).exists())

    def test_cohort_members_link_to_the_course_session_unit(self):
        check_event_reminders()

        bell_urls = dict(Notification.objects.filter(
            notification_type='event_reminder',
        ).values_list('user__email', 'url'))
        self.assertEqual(bell_urls['member@test.com'], self.unit_path)
        self.assertEqual(bell_urls['both@test.com'], self.unit_path)
        self.assertEqual(bell_urls['registrant@test.com'], self.event.get_absolute_url())

        stub = StubSESClient()
        with patch(
            'community_base.mail.backends.ses_local.configured_client',
            return_value=stub,
        ):
            deliver_pending_mail()
        html = {
            call['Destination']['ToAddresses'][0]: call['Content']['Simple']['Body']['Html']['Data']
            for call in stub.calls
        }
        unit_url = f'https://aishippinglabs.com{self.unit_path}'
        join_url = f'https://aishippinglabs.com{self.event.get_join_url()}'
        self.assertIn(f'href="{unit_url}"', html['member@test.com'])
        self.assertNotIn(join_url, html['member@test.com'])
        self.assertIn(f'href="{join_url}"', html['registrant@test.com'])
        self.assertNotIn(self.unit.get_absolute_url(), html['registrant@test.com'])

    def test_toggle_off_reminds_registrants_only(self):
        IntegrationSetting.objects.create(
            key='EVENT_REMINDERS_INCLUDE_COHORT', value='false', group='site',
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

        check_event_reminders()

        self.assertEqual(self._reminded_ids(), {self.registrant.pk, self.both.pk})

    def test_preview_command_lists_audience_and_writes_nothing(self):
        out = StringIO()
        call_command('preview_event_reminders', str(self.event.pk), stdout=out)
        output = out.getvalue()

        member_line = next(line for line in output.splitlines() if 'member@test.com' in line)
        self.assertIn('would_send', member_line)
        self.assertIn(f'link=https://aishippinglabs.com{self.unit_path}', member_line)
        bounced_line = next(line for line in output.splitlines() if 'bounced@test.com' in line)
        self.assertIn('skipped_permanent_bounce', bounced_line)
        self.assertNotIn('self-paced@test.com', output)
        self.assertIn('eligible=5 would_email=3', output)
        self.assertFalse(EventReminderLog.objects.exists())
        self.assertFalse(EmailDelivery.objects.exists())
