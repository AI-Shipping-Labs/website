"""Studio tests for the CRM calls pages and contact-record sections.

Issue #1949 AC5-AC12 + AC17: calls list/detail/new/edit, the Calls and
Member Q&A sections on the contact detail, and access control. Header
link journeys are Playwright-only per the issue's test table.
"""

import datetime
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comments.models import Comment
from content.models import Course, Module, Unit
from crm.models import CRMRecord
from crm.services.calls import record_call

User = get_user_model()


def _when(minute=0):
    return timezone.make_aware(datetime.datetime(2026, 10, 13, 15, minute))


class CRMCallViewsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(
            user=cls.member, created_by=cls.staff,
        )
        cls.call = record_call(
            email=cls.member.email,
            occurred_at=_when(),
            summary='Discussed the sprint plan and next steps.',
            created_by=cls.staff,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')


class CRMCallsListTest(CRMCallViewsBase):
    def test_lists_calls_most_recent_first_with_summary_and_source(self):
        older = record_call(
            email='member@test.com',
            occurred_at=timezone.make_aware(
                datetime.datetime(2026, 10, 13, 14, 0),
            ),
            summary='Earlier call',
            source='booking',
            source_ref='BK-LIST-1',
        )
        response = self.client.get(reverse('studio_crm_calls'))
        content = response.content.decode()
        self.assertLess(
            content.index(f'data-testid="crm-calls-row-{self.call.pk}"'),
            content.index(f'data-testid="crm-calls-row-{older.pk}"'),
        )
        self.assertContains(response, 'Discussed the sprint plan')
        self.assertContains(response, 'member@test.com')

    def test_search_filters_on_email(self):
        record_call(
            email='other@test.com',
            occurred_at=_when(minute=1),
            summary='Unrelated',
        )
        response = self.client.get(
            reverse('studio_crm_calls'), {'q': 'other@'},
        )
        self.assertNotContains(response, 'Discussed the sprint plan')
        self.assertContains(response, 'Unrelated')

    def test_search_without_hits_renders_empty_state(self):
        response = self.client.get(
            reverse('studio_crm_calls'), {'q': 'nobody-here@'},
        )
        self.assertNotContains(response, 'data-testid="crm-calls-table"')


class CRMCallDetailTest(CRMCallViewsBase):
    def test_shows_time_source_notes_created_by_and_contact(self):
        response = self.client.get(
            reverse('studio_crm_call_detail', kwargs={'call_id': self.call.pk}),
        )
        self.assertContains(response, 'data-testid="crm-call-detail-summary"')
        self.assertContains(response, 'Discussed the sprint plan')
        self.assertContains(response, 'data-testid="crm-call-detail-source"')
        self.assertContains(response, 'staff@test.com')
        self.assertContains(
            response,
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )
        self.assertContains(
            response,
            reverse('studio_crm_call_edit', kwargs={'call_id': self.call.pk}),
        )


class CRMCallNewTest(CRMCallViewsBase):
    def test_contact_detail_offers_log_call_action(self):
        response = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )
        self.assertContains(response, 'data-testid="crm-detail-log-call"')
        self.assertContains(
            response,
            reverse('studio_crm_call_new', kwargs={'crm_id': self.record.pk}),
        )

    def test_get_renders_form(self):
        response = self.client.get(
            reverse('studio_crm_call_new', kwargs={'crm_id': self.record.pk}),
        )
        self.assertContains(response, 'data-testid="crm-call-form-section"')
        self.assertContains(response, 'type="datetime-local"')

    def test_post_creates_manual_call_and_redirects_to_contact(self):
        url = reverse(
            'studio_crm_call_new', kwargs={'crm_id': self.record.pk},
        )
        response = self.client.post(url, {
            'occurred_at': '2026-10-13T14:30',
            'summary': 'Talked about homework blocks.',
        })
        call = self.member_contact_calls().get(
            summary='Talked about homework blocks.',
        )
        self.assertEqual(call.source, 'manual')
        self.assertEqual(call.created_by, self.staff)
        self.assertEqual(
            call.occurred_at.strftime('%Y-%m-%d %H:%M'), '2026-10-13 14:30',
        )
        # AC7: back to the contact detail, where the Calls section lists it.
        self.assertRedirects(
            response,
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )

    def test_post_without_datetime_rerenders_with_error(self):
        url = reverse(
            'studio_crm_call_new', kwargs={'crm_id': self.record.pk},
        )
        response = self.client.post(url, {'occurred_at': '', 'summary': 'x'})
        self.assertContains(response, 'data-testid="crm-call-form-error"')
        self.assertFalse(
            self.member_contact_calls().filter(summary='x').exists(),
        )

    def member_contact_calls(self):
        from crm.services.calls import calls_for_member
        return calls_for_member(self.member)


class CRMCallEditTest(CRMCallViewsBase):
    def test_edit_updates_time_summary_and_source(self):
        url = reverse(
            'studio_crm_call_edit', kwargs={'call_id': self.call.pk},
        )
        response = self.client.post(url, {
            'occurred_at': '2026-10-14T09:00',
            'summary': 'Updated notes after the call.',
            'source': 'calendly',
        })
        self.assertRedirects(
            response,
            reverse('studio_crm_call_detail', kwargs={'call_id': self.call.pk}),
        )
        self.call.refresh_from_db()
        self.assertEqual(
            self.call.occurred_at.strftime('%Y-%m-%d %H:%M'),
            '2026-10-14 09:00',
        )
        self.assertEqual(self.call.summary, 'Updated notes after the call.')
        self.assertEqual(self.call.source, 'calendly')

    def test_get_prefills_existing_values(self):
        url = reverse(
            'studio_crm_call_edit', kwargs={'call_id': self.call.pk},
        )
        response = self.client.get(url)
        self.assertContains(response, 'Discussed the sprint plan')
        self.assertContains(response, 'value="manual"')


class CRMContactDetailSectionsTest(CRMCallViewsBase):
    """AC9-AC12: Calls + Member Q&A sections; booked calls and onboarding
    sections survive unchanged."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            content_id=uuid.uuid4(), title='AI Hero', slug='aihero',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Day 1', slug='day-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            content_id=uuid.uuid4(), module=cls.module,
            title='Frontmatter', slug='frontmatter', sort_order=1,
        )
        cls.question = Comment.objects.create(
            content_id=cls.unit.content_id,
            user=cls.member,
            body='How do I unblock the checkpoint?',
        )
        cls.answer = Comment.objects.create(
            content_id=cls.unit.content_id,
            user=cls.member,
            parent=cls.question,
            body='Solved it with the docs.',
        )
        cls.hidden = Comment.objects.create(
            content_id=cls.unit.content_id,
            user=cls.member,
            body='Hidden spam answer',
        )
        cls.hidden.hidden_at = timezone.now()
        cls.hidden.save(update_fields=['hidden_at'])

    def test_calls_section_lists_member_calls_with_log_and_edit(self):
        response = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )
        self.assertContains(response, 'data-testid="crm-calls-section"')
        self.assertContains(response, 'data-testid="crm-call-item"')
        self.assertContains(
            response,
            reverse(
                'studio_crm_call_detail', kwargs={'call_id': self.call.pk},
            ),
        )
        self.assertContains(
            response,
            reverse('studio_crm_call_new', kwargs={'crm_id': self.record.pk}),
        )

    def test_booked_calls_and_onboarding_sections_still_render(self):
        response = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )
        # AC10: the Calendly section stays in place untouched.
        self.assertContains(
            response, 'data-testid="crm-booked-calls-section"',
        )
        # AC12: the onboarding section is unchanged and still present.
        self.assertContains(
            response, 'data-testid="crm-onboarding-section"',
        )

    def test_qa_section_shows_questions_and_answers_with_context(self):
        response = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk}),
        )
        self.assertContains(response, 'data-testid="crm-qna-section"')
        self.assertContains(response, 'data-testid="crm-qna-item"')
        self.assertContains(response, 'How do I unblock the checkpoint?')
        self.assertContains(response, 'Course unit')
        self.assertContains(response, 'data-testid="crm-qna-thread-link"')
        # Hidden comments are excluded, not marked.
        self.assertNotContains(response, 'Hidden spam answer')
        self.assertNotContains(response, 'data-testid="crm-qna-hidden"')

    def test_qa_empty_state_for_member_without_comments(self):
        quiet = User.objects.create_user(
            email='quiet@test.com', password='pw',
        )
        record = CRMRecord.objects.create(user=quiet, created_by=self.staff)
        response = self.client.get(
            reverse('studio_crm_detail', kwargs={'crm_id': record.pk}),
        )
        self.assertContains(response, 'data-testid="crm-qna-empty"')


class CRMCallAccessControlTest(TestCase):
    """Anonymous users redirect to login; non-staff get 403 (AC5-AC8)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='gate-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='gate-member@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(
            user=cls.member, created_by=cls.staff,
        )
        cls.call = record_call(
            email=cls.member.email,
            occurred_at=_when(),
            summary='secret notes',
        )

    def test_anonymous_is_redirected_to_login(self):
        urls = [
            reverse('studio_crm_calls'),
            reverse(
                'studio_crm_call_detail', kwargs={'call_id': self.call.pk},
            ),
            reverse(
                'studio_crm_call_edit', kwargs={'call_id': self.call.pk},
            ),
            reverse(
                'studio_crm_call_new', kwargs={'crm_id': self.record.pk},
            ),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertTrue(
                response.url.startswith('/accounts/login/'), url,
            )

    def test_non_staff_gets_403(self):
        User.objects.create_user(email='pleb@test.com', password='pw')
        self.client.login(email='pleb@test.com', password='pw')
        urls = [
            reverse('studio_crm_calls'),
            reverse(
                'studio_crm_call_detail', kwargs={'call_id': self.call.pk},
            ),
            reverse(
                'studio_crm_call_edit', kwargs={'call_id': self.call.pk},
            ),
            reverse(
                'studio_crm_call_new', kwargs={'crm_id': self.record.pk},
            ),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)


class CRMCallDraftBase(CRMCallViewsBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from booking.models.booking import Booking
        from booking.models.host import BookingHost

        cls.host, _ = BookingHost.objects.get_or_create(
            slug='alexey', defaults={'name': 'Alexey'},
        )
        start = timezone.now() - datetime.timedelta(hours=2)
        cls.booking = Booking.objects.create(
            host=cls.host,
            invitee_name='Jane Doe',
            invitee_email=cls.member.email,
            start_datetime=start,
            end_datetime=start + datetime.timedelta(minutes=30),
            zoom_meeting_id='zm-1',
        )
        cls.booking_call = record_call(
            email=cls.member.email,
            occurred_at=start,
            summary='Booked: intro call',
            source='booking',
            source_ref=str(cls.booking.id),
        )

    @classmethod
    def add_draft(cls, call):
        call.email_draft_subject = 'Your 1:1 with Alexey'
        call.email_draft_body = 'Hi! Here is what we covered.'
        call.email_draft_ready_at = timezone.now()
        call.save(
            update_fields=[
                'email_draft_subject', 'email_draft_body',
                'email_draft_ready_at',
            ],
        )
        return call


class CRMCallDraftSectionTest(CRMCallDraftBase):
    """AC18: the draft section renders for booking calls with capture
    state and stays hidden for manual/zoom calls without one."""

    def test_ready_draft_renders_section_with_send_button(self):
        self.add_draft(self.booking_call)
        response = self.client.get(
            reverse(
                'studio_crm_call_detail',
                kwargs={'call_id': self.booking_call.pk},
            ),
        )
        self.assertContains(response, 'data-testid="crm-call-email-draft"')
        self.assertContains(response, 'data-testid="crm-call-email-draft-subject"')
        self.assertContains(response, 'Your 1:1 with Alexey')
        self.assertContains(response, 'Hi! Here is what we covered.')
        self.assertContains(response, 'data-testid="crm-call-email-send"')
        self.assertContains(
            response,
            reverse(
                'studio_crm_call_send_email',
                kwargs={'call_id': self.booking_call.pk},
            ),
        )

    def test_manual_call_without_capture_state_has_no_section(self):
        response = self.client.get(
            reverse(
                'studio_crm_call_detail', kwargs={'call_id': self.call.pk},
            ),
        )
        self.assertNotContains(
            response, 'data-testid="crm-call-email-draft"',
        )

    def test_zoom_call_without_draft_has_no_section(self):
        zoom_call = record_call(
            email=self.member.email,
            occurred_at=_when(minute=5),
            summary='Imported summary.',
            source='zoom',
            source_ref='2026-04-23-jane-doe-summary',
        )
        response = self.client.get(
            reverse(
                'studio_crm_call_detail', kwargs={'call_id': zoom_call.pk},
            ),
        )
        self.assertNotContains(
            response, 'data-testid="crm-call-email-draft"',
        )

    def test_unavailable_capture_shows_status_without_send(self):
        self.booking_call.capture_unavailable_at = timezone.now()
        self.booking_call.save(
            update_fields=['capture_unavailable_at'],
        )
        response = self.client.get(
            reverse(
                'studio_crm_call_detail',
                kwargs={'call_id': self.booking_call.pk},
            ),
        )
        self.assertContains(response, 'data-testid="crm-call-email-draft"')
        self.assertContains(response, 'Unavailable')
        self.assertNotContains(response, 'data-testid="crm-call-email-send"')
        self.assertNotContains(
            response, 'data-testid="crm-call-email-draft-subject"',
        )


class CRMCallSendEmailTest(CRMCallDraftBase):
    """AC17/AC19: the explicit Studio POST is the only send path; a
    double POST sends exactly once."""

    def test_send_delivers_exactly_once_and_redirects(self):
        self.add_draft(self.booking_call)
        url = reverse(
            'studio_crm_call_send_email',
            kwargs={'call_id': self.booking_call.pk},
        )
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse(
                'studio_crm_call_detail',
                kwargs={'call_id': self.booking_call.pk},
            ),
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.member.email])
        self.assertEqual(mail.outbox[0].subject, 'Your 1:1 with Alexey')
        self.booking_call.refresh_from_db()
        self.assertIsNotNone(self.booking_call.email_sent_at)

        # Double POST: re-checked, no second email.
        response = self.client.post(url)
        self.assertEqual(len(mail.outbox), 1)
        self.booking_call.refresh_from_db()
        self.assertIsNotNone(self.booking_call.email_sent_at)

        # The detail page shows the sent marker and no send button.
        detail = self.client.get(
            reverse(
                'studio_crm_call_detail',
                kwargs={'call_id': self.booking_call.pk},
            ),
        )
        self.assertContains(detail, 'data-testid="crm-call-email-sent"')
        self.assertNotContains(detail, 'data-testid="crm-call-email-send"')

    def test_send_without_ready_draft_sends_nothing(self):
        url = reverse(
            'studio_crm_call_send_email',
            kwargs={'call_id': self.booking_call.pk},
        )
        self.client.post(url)
        self.assertEqual(len(mail.outbox), 0)
        self.booking_call.refresh_from_db()
        self.assertIsNone(self.booking_call.email_sent_at)

    def test_mail_backend_failure_keeps_draft_unsent(self):
        self.add_draft(self.booking_call)
        url = reverse(
            'studio_crm_call_send_email',
            kwargs={'call_id': self.booking_call.pk},
        )
        with mock.patch(
            'studio.views.crm.send_mail', side_effect=RuntimeError('SES down'),
        ):
            self.client.post(url)
        self.assertEqual(len(mail.outbox), 0)
        self.booking_call.refresh_from_db()
        self.assertIsNone(self.booking_call.email_sent_at)


class CRMCallDraftAccessControlTest(CRMCallDraftBase):
    """Non-staff cannot send; nothing goes out (AC19)."""

    def test_non_staff_send_returns_403_and_sends_nothing(self):
        self.add_draft(self.booking_call)
        User.objects.create_user(email='pleb2@test.com', password='pw')
        self.client.login(email='pleb2@test.com', password='pw')
        url = reverse(
            'studio_crm_call_send_email',
            kwargs={'call_id': self.booking_call.pk},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_anonymous_send_redirects_to_login(self):
        self.client.logout()
        url = reverse(
            'studio_crm_call_send_email',
            kwargs={'call_id': self.booking_call.pk},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/accounts/login/'))
        self.assertEqual(len(mail.outbox), 0)


class CRMCallEditDraftFieldsTest(CRMCallDraftBase):
    """AC20: the edit form can change the draft before sending."""

    def test_edit_form_renders_and_saves_draft_fields(self):
        self.add_draft(self.booking_call)
        url = reverse(
            'studio_crm_call_edit',
            kwargs={'call_id': self.booking_call.pk},
        )
        response = self.client.get(url)
        self.assertContains(
            response, 'data-testid="crm-call-email-draft-subject-input"',
        )
        self.assertContains(
            response, 'data-testid="crm-call-email-draft-body-input"',
        )
        response = self.client.post(url, {
            'occurred_at': self.booking_call.occurred_at.strftime(
                '%Y-%m-%dT%H:%M',
            ),
            'summary': 'Booked: intro call',
            'source': 'booking',
            'email_draft_subject': 'Edited subject',
            'email_draft_body': 'Edited body.',
        })
        self.assertRedirects(
            response,
            reverse(
                'studio_crm_call_detail',
                kwargs={'call_id': self.booking_call.pk},
            ),
        )
        self.booking_call.refresh_from_db()
        self.assertEqual(self.booking_call.email_draft_subject, 'Edited subject')
        self.assertEqual(self.booking_call.email_draft_body, 'Edited body.')
        # Editing the draft must not mark it sent.
        self.assertIsNone(self.booking_call.email_sent_at)

    def test_edit_form_hides_draft_fields_without_a_draft(self):
        url = reverse(
            'studio_crm_call_edit',
            kwargs={'call_id': self.booking_call.pk},
        )
        response = self.client.get(url)
        self.assertNotContains(
            response, 'data-testid="crm-call-email-draft-subject-input"',
        )
