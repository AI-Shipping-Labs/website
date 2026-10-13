"""Studio tests for the CRM calls pages and contact-record sections.

Issue #1949 AC5-AC12 + AC17: calls list/detail/new/edit, the Calls and
Member Q&A sections on the contact detail, and access control. Header
link journeys are Playwright-only per the issue's test table.
"""

import datetime
import uuid

from django.contrib.auth import get_user_model
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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Discussed the sprint plan')
        self.assertContains(response, 'Unrelated')

    def test_search_without_hits_renders_empty_state(self):
        response = self.client.get(
            reverse('studio_crm_calls'), {'q': 'nobody-here@'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="crm-calls-table"')


class CRMCallDetailTest(CRMCallViewsBase):
    def test_shows_time_source_notes_created_by_and_contact(self):
        response = self.client.get(
            reverse('studio_crm_call_detail', kwargs={'call_id': self.call.pk}),
        )
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
