"""Studio surfacing tests for `#plan-sprints` ingest (issue #889, Phase 1).

Verifies staff see a member's captured threads on the CRM detail page,
the empty state when nothing is captured, and the unmatched-thread
review surface. Member-facing leakage is covered by the dedicated
visibility test below.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import CRMRecord, SlackMessage, SlackThread
from crm.services.slack_note_sync import sync_thread_to_interview_note
from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()

CHANNEL = 'C_TEST'


def _thread_with_messages(member=None, plan=None, thread_ts='1700000000.000001',
                          texts=None, slack_user_id='U_AUTHOR'):
    texts = texts or ['Root update']
    thread = SlackThread.objects.create(
        channel_id=CHANNEL,
        thread_ts=thread_ts,
        slack_user_id=slack_user_id,
        member=member,
        plan=plan,
        posted_at=timezone.now(),
        permalink='https://slack.example/p1',
        reply_count=max(len(texts) - 1, 0),
    )
    base = float(thread_ts)
    for i, text in enumerate(texts):
        SlackMessage.objects.create(
            thread=thread,
            ts=f'{base + i:.6f}',
            slack_user_id=slack_user_id if i == 0 else f'U_REPLY{i}',
            author_display='Author' if i == 0 else f'Replier {i}',
            text=text,
            posted_at=timezone.now(),
            is_root=(i == 0),
        )
    return thread


class SlackUpdatesOnCRMDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', slack_user_id='U_AUTHOR',
        )
        cls.record = CRMRecord.objects.create(user=cls.member)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_synced_thread_shows_as_member_note_on_crm_detail(self):
        thread = _thread_with_messages(
            member=self.member,
            texts=['Root update', 'A reply', 'Another reply'],
        )
        sync_thread_to_interview_note(thread)
        url = reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="crm-slack-updates-section"')
        self.assertContains(response, 'data-testid="member-notes-section"')
        self.assertContains(response, 'data-testid="member-note-tag"', count=2)
        self.assertContains(response, 'Root update')
        self.assertContains(response, 'A reply')
        self.assertContains(response, 'Author')
        self.assertEqual(InterviewNote.objects.count(), 1)

    def test_no_separate_slack_empty_state_when_no_threads(self):
        url = reverse('studio_crm_detail', kwargs={'crm_id': self.record.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="crm-slack-updates-empty"')
        self.assertNotContains(response, 'data-testid="crm-slack-thread"')


class SlackUpdatesOnPlanDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff2@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='pm@test.com', password='pw', slack_user_id='U_AUTHOR',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def setUp(self):
        self.client.login(email='staff2@test.com', password='pw')

    def test_plan_linked_thread_shows_on_plan_detail(self):
        _thread_with_messages(member=self.member, plan=self.plan)
        url = reverse('studio_plan_detail', kwargs={'plan_id': self.plan.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-slack-updates-section"')
        self.assertContains(response, 'data-testid="crm-slack-thread"')
        self.assertNotContains(response, 'data-testid="crm-slack-message"')


class SlackIngestReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff3@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff3@test.com', password='pw')

    def test_unmatched_thread_listed_on_review_surface(self):
        _thread_with_messages(
            member=None,
            texts=['Update from a stranger'],
            slack_user_id='U_NOBODY',
        )
        response = self.client.get(reverse('studio_crm_slack_ingest'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="slack-ingest-unmatched-thread"',
        )
        self.assertContains(response, 'Update from a stranger')
        self.assertContains(
            response, 'data-testid="slack-ingest-unmatched-flag"',
        )

    def test_matched_thread_not_in_unmatched_list(self):
        member = User.objects.create_user(
            email='matched@test.com', password='pw', slack_user_id='U_AUTHOR',
        )
        _thread_with_messages(member=member, texts=['Matched update'])
        response = self.client.get(reverse('studio_crm_slack_ingest'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="slack-ingest-unmatched-empty"',
        )
        self.assertNotContains(
            response, 'data-testid="slack-ingest-unmatched-thread"',
        )

    def test_review_surface_requires_staff(self):
        self.client.logout()
        User.objects.create_user(
            email='plain@test.com', password='pw',
        )
        self.client.login(email='plain@test.com', password='pw')
        response = self.client.get(reverse('studio_crm_slack_ingest'))
        # staff_required redirects non-staff away from Studio.
        self.assertNotEqual(response.status_code, 200)
