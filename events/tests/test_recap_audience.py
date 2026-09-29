"""Recap-ready audience: registrants, linked cohorts and book clubs.

The recap-ready notice reaches everyone interested in an event, not only
its registrants. These tests cover the audience union, the reasons a dry
run reports, the unsubscribe rule for readers without an explicit sign-up,
idempotency across the wider audience, and the recording link.
"""

import datetime
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, tag
from django.utils import timezone

from bookclub.models import Book, Chapter, ChapterRead, Note
from content.models import Cohort, CohortEnrollment, Course
from email_app.testing import StubSESClient, deliver_pending_mail
from events.models import Event, EventRegistration, EventSeries
from events.services.event_recap_notification import (
    notify_recap_ready,
    preview_recap_audience,
)
from notifications.models import EventReminderLog

User = get_user_model()


def _reasons(item):
    return {(reason['source'], reason['label']) for reason in item['reasons']}


@tag('core')
class RecapAudienceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.series = EventSeries.objects.create(
            name='Buildcamp Office Hours', slug='buildcamp-office-hours',
        )
        cls.other_series = EventSeries.objects.create(
            name='Other Series', slug='other-series',
        )
        cls.event = Event.objects.create(
            title='Office Hours: Session 2',
            slug='office-hours-session-2',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            event_series=cls.series,
            recap_notes='## What we covered\n\nEvaluation.',
        )
        course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-recap-audience', status='published',
        )
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=cls.series,
        )
        unrelated_cohort = Cohort.objects.create(
            course=course, name='Cohort 3',
            start_date=datetime.date(2026, 5, 1),
            end_date=datetime.date(2026, 8, 1),
            event_series=cls.other_series,
        )
        book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering-recap',
            author='Philip Kiely', status='current',
            start_date=datetime.date(2026, 8, 10),
        )
        chapter = Chapter.objects.create(
            book=book, number=1, title='Serving', event=cls.event,
        )

        def user(name, **kwargs):
            return User.objects.create_user(
                email=f'{name}@test.com', email_verified=True, **kwargs,
            )

        cls.attendee = user('attendee')
        cls.registrant = user('registrant')
        cls.cohort_member = user('cohort-member', unsubscribed=True)
        cls.both = user('both')
        cls.reader = user('reader')
        cls.note_writer = user('note-writer')
        cls.unsubscribed_reader = user('unsubscribed-reader', unsubscribed=True)
        cls.inactive_member = user('inactive-member', is_active=False)
        cls.other_cohort_member = user('other-cohort-member')
        cls.unrelated = user('unrelated')

        EventRegistration.objects.create(
            event=cls.event, user=cls.attendee, joined_at=now - timedelta(hours=2),
        )
        EventRegistration.objects.create(event=cls.event, user=cls.registrant)
        EventRegistration.objects.create(event=cls.event, user=cls.both)
        for member in (cls.cohort_member, cls.both, cls.inactive_member):
            CohortEnrollment.objects.create(cohort=cohort, user=member)
        CohortEnrollment.objects.create(
            cohort=unrelated_cohort, user=cls.other_cohort_member,
        )
        ChapterRead.objects.create(chapter=chapter, user=cls.reader)
        ChapterRead.objects.create(chapter=chapter, user=cls.unsubscribed_reader)
        Note.objects.create(chapter=chapter, user=cls.note_writer, body='Takeaway.')

    def test_dry_run_lists_union_with_reasons_and_sends_nothing(self):
        preview = preview_recap_audience(self.event)

        self.assertTrue(preview['dry_run'])
        self.assertTrue(preview['ready'])
        by_user = {item['user_id']: item for item in preview['results']}
        self.assertEqual(set(by_user), {
            self.attendee.pk, self.registrant.pk, self.cohort_member.pk,
            self.both.pk, self.reader.pk, self.note_writer.pk,
            self.unsubscribed_reader.pk,
        })
        cohort_reason = ('cohort', 'Buildcamp - Cohort 4')
        book_reason = ('book_club', 'Inference Engineering')
        self.assertEqual(_reasons(by_user[self.attendee.pk]), {('attended', '')})
        self.assertEqual(_reasons(by_user[self.registrant.pk]), {('registered', '')})
        self.assertEqual(_reasons(by_user[self.cohort_member.pk]), {cohort_reason})
        self.assertEqual(
            _reasons(by_user[self.both.pk]), {('registered', ''), cohort_reason},
        )
        self.assertEqual(_reasons(by_user[self.reader.pk]), {book_reason})
        self.assertEqual(_reasons(by_user[self.note_writer.pk]), {book_reason})
        self.assertEqual(by_user[self.both.pk]['email'], 'both@test.com')

        # A cohort enrollment is an explicit sign-up: newsletter unsubscribe
        # does not suppress it. A book-club reader has no sign-up: it does.
        self.assertEqual(by_user[self.cohort_member.pk]['email_status'], 'would_send')
        self.assertEqual(
            by_user[self.unsubscribed_reader.pk]['email_status'],
            'skipped_unsubscribed',
        )
        self.assertEqual(preview['would_email'], 6)
        self.assertEqual(preview['skipped'], 1)
        self.assertEqual(
            preview['by_reason'],
            {'attended': 1, 'book_club': 3, 'cohort': 2, 'registered': 2},
        )
        self.assertFalse(EmailDelivery.objects.exists())
        self.assertFalse(EventReminderLog.objects.exists())

    def test_send_emails_each_person_once_and_rerun_never_double_emails(self):
        result = notify_recap_ready(self.event)

        self.assertFalse(result['dry_run'])
        self.assertEqual(result['eligible'], 7)
        self.assertEqual(result['emailed'], 6)
        self.assertEqual(result['failed'], 0)
        recipients = list(
            EmailDelivery.objects.values_list('recipient_email', flat=True),
        )
        self.assertEqual(len(recipients), 6)
        self.assertEqual(len(set(recipients)), 6)
        self.assertNotIn('unsubscribed-reader@test.com', recipients)
        self.assertNotIn('other-cohort-member@test.com', recipients)
        self.assertNotIn('unrelated@test.com', recipients)
        self.assertNotIn('inactive-member@test.com', recipients)

        rerun = notify_recap_ready(self.event)
        self.assertEqual(rerun['emailed'], 0)
        self.assertEqual(rerun['already_emailed'], 6)
        self.assertEqual(EmailDelivery.objects.count(), 6)
        preview = preview_recap_audience(self.event)
        self.assertEqual(preview['would_email'], 0)
        self.assertEqual(preview['already_emailed'], 6)

    def test_rerun_reaches_a_cohort_member_enrolled_after_the_first_send(self):
        notify_recap_ready(self.event)
        late = User.objects.create_user(email='late@test.com', email_verified=True)
        cohort = Cohort.objects.get(name='Cohort 4')
        CohortEnrollment.objects.create(cohort=cohort, user=late)

        rerun = notify_recap_ready(self.event)

        self.assertEqual(rerun['emailed'], 1)
        self.assertTrue(
            EmailDelivery.objects.filter(recipient_email='late@test.com').exists(),
        )

    def test_dry_run_reports_not_ready_instead_of_failing(self):
        Event.objects.filter(pk=self.event.pk).update(recap_notes='', recap_notes_html='')
        self.event.refresh_from_db()

        preview = preview_recap_audience(self.event)

        self.assertFalse(preview['ready'])
        self.assertEqual(preview['reason_code'], 'missing_recap')
        self.assertEqual(preview['eligible'], 7)

    def _rendered_html(self):
        stub = StubSESClient()
        with patch(
            'community_base.mail.backends.ses_local.configured_client',
            return_value=stub,
        ):
            deliver_pending_mail()
        return [call['Content']['Simple']['Body']['Html']['Data'] for call in stub.calls]

    def test_email_links_recording_when_the_event_has_one(self):
        Event.objects.filter(pk=self.event.pk).update(
            recording_url='https://www.youtube.com/watch?v=abc',
        )
        self.event.refresh_from_db()
        notify_recap_ready(self.event)

        html = self._rendered_html()[0]
        event_url = f'https://aishippinglabs.com{self.event.get_absolute_url()}'
        self.assertIn(f'href="{event_url}">Watch the recording', html)

    def test_email_omits_recording_link_without_a_recording(self):
        notify_recap_ready(self.event)

        html = self._rendered_html()[0]
        self.assertNotIn('Watch the recording', html)


@tag('core')
class NotifyRecapReadyCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.event = Event.objects.create(
            title='Recap Command Event',
            slug='recap-command-event',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            recap_notes='Notes.',
        )
        cls.member = User.objects.create_user(
            email='command-member@test.com', email_verified=True,
        )
        EventRegistration.objects.create(event=cls.event, user=cls.member)

    def test_dry_run_prints_audience_and_sends_nothing(self):
        out = StringIO()
        call_command('notify_recap_ready', str(self.event.pk), '--dry-run', stdout=out)

        output = out.getvalue()
        self.assertIn('command-member@test.com\twould_send\tregistered', output)
        self.assertIn('eligible=1 would_email=1', output)
        self.assertFalse(EmailDelivery.objects.exists())

    def test_send_then_rerun_reports_already_sent(self):
        first, second = StringIO(), StringIO()
        call_command('notify_recap_ready', str(self.event.pk), stdout=first)
        call_command('notify_recap_ready', str(self.event.pk), stdout=second)

        self.assertIn('emailed=1', first.getvalue())
        self.assertIn('emailed=0', second.getvalue())
        self.assertIn('already_sent=1', second.getvalue())
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_unknown_event_and_not_ready_event_fail(self):
        with self.assertRaisesMessage(CommandError, 'No event with id=999999'):
            call_command('notify_recap_ready', '999999')
        Event.objects.filter(pk=self.event.pk).update(recap_notes='', recap_notes_html='')
        with self.assertRaisesMessage(CommandError, 'missing_recap'):
            call_command('notify_recap_ready', str(self.event.pk))
