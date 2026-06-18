"""Undo-endpoint tests for Phase 2 reversal (issue #890).

Covers staff-only + POST-only access control on both undo views and that
the panel renders the auto-applied summary/blockers + undo controls for a
thread with an event (and not for a thread without one).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    AppliedProgressChange,
    CRMRecord,
    IngestedProgressEvent,
    SlackMessage,
    SlackThread,
)
from plans.models import Checkpoint, Deliverable, Plan, Sprint, Week

User = get_user_model()

CHANNEL = 'C_TEST'


class UndoViewsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', slack_user_id='U_AUTHOR',
        )
        cls.record = CRMRecord.objects.create(user=cls.member)
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)

    def _event_with_changes(self):
        thread = SlackThread.objects.create(
            channel_id=CHANNEL, thread_ts='1700000000.000001',
            slack_user_id='U_AUTHOR', member=self.member, plan=self.plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread, ts='1700000000.000001', slack_user_id='U_AUTHOR',
            text='done', posted_at=timezone.now(), is_root=True,
        )
        event = IngestedProgressEvent.objects.create(
            thread=thread, plan=self.plan, summary='Summary text',
            blockers=['blocked on review'],
            source_message_ts='1700000000.000001',
        )
        cp = Checkpoint.objects.create(week=self.week, description='cp')
        deliv = Deliverable.objects.create(plan=self.plan, description='d')
        now = timezone.now()
        cp.done_at = now
        cp.save(update_fields=['done_at'])
        deliv.done_at = now
        deliv.save(update_fields=['done_at'])
        cp_change = AppliedProgressChange.objects.create(
            event=event, item_kind='checkpoint', checkpoint=cp,
            previous_done_at=None,
        )
        deliv_change = AppliedProgressChange.objects.create(
            event=event, item_kind='deliverable', deliverable=deliv,
            previous_done_at=None,
        )
        return event, cp, deliv, cp_change, deliv_change


class UndoAccessControlTests(UndoViewsBase):
    def test_event_undo_rejects_non_staff(self):
        event, cp, *_ = self._event_with_changes()
        self.client.login(email='member@test.com', password='pw')
        url = reverse(
            'studio_crm_slack_progress_undo', kwargs={'event_id': event.pk},
        )
        response = self.client.post(url)
        self.assertIn(response.status_code, (302, 403, 404))
        # The item stays done — no reversal happened.
        cp.refresh_from_db()
        self.assertIsNotNone(cp.done_at)
        self.assertTrue(
            IngestedProgressEvent.objects.filter(pk=event.pk).exists()
        )

    def test_change_undo_rejects_non_staff(self):
        event, cp, _d, cp_change, _dc = self._event_with_changes()
        self.client.login(email='member@test.com', password='pw')
        url = reverse(
            'studio_crm_slack_progress_change_undo',
            kwargs={'change_id': cp_change.pk},
        )
        response = self.client.post(url)
        self.assertIn(response.status_code, (302, 403, 404))
        cp.refresh_from_db()
        self.assertIsNotNone(cp.done_at)
        self.assertTrue(
            AppliedProgressChange.objects.filter(pk=cp_change.pk).exists()
        )

    def test_event_undo_rejects_get(self):
        event, *_ = self._event_with_changes()
        self.client.login(email='staff@test.com', password='pw')
        url = reverse(
            'studio_crm_slack_progress_undo', kwargs={'event_id': event.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            IngestedProgressEvent.objects.filter(pk=event.pk).exists()
        )

    def test_change_undo_rejects_get(self):
        _e, _cp, _d, cp_change, _dc = self._event_with_changes()
        self.client.login(email='staff@test.com', password='pw')
        url = reverse(
            'studio_crm_slack_progress_change_undo',
            kwargs={'change_id': cp_change.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            AppliedProgressChange.objects.filter(pk=cp_change.pk).exists()
        )


class UndoStaffActionTests(UndoViewsBase):
    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_staff_event_undo_reverts_all_and_deletes_event(self):
        event, cp, deliv, *_ = self._event_with_changes()
        url = reverse(
            'studio_crm_slack_progress_undo', kwargs={'event_id': event.pk},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        cp.refresh_from_db()
        deliv.refresh_from_db()
        self.assertIsNone(cp.done_at)
        self.assertIsNone(deliv.done_at)
        self.assertFalse(
            IngestedProgressEvent.objects.filter(pk=event.pk).exists()
        )

    def test_staff_single_change_undo_reverts_only_that_change(self):
        event, cp, deliv, cp_change, _dc = self._event_with_changes()
        url = reverse(
            'studio_crm_slack_progress_change_undo',
            kwargs={'change_id': cp_change.pk},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        cp.refresh_from_db()
        deliv.refresh_from_db()
        self.assertIsNone(cp.done_at)
        self.assertIsNotNone(deliv.done_at)
        self.assertTrue(
            IngestedProgressEvent.objects.filter(pk=event.pk).exists()
        )
        self.assertEqual(event.changes.count(), 1)


class PanelRenderTests(UndoViewsBase):
    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_panel_renders_autoapply_block_and_controls(self):
        event, *_ = self._event_with_changes()
        url = reverse('studio_plan_detail', kwargs={'plan_id': self.plan.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-slack-autoapply"')
        self.assertContains(response, 'Summary text')
        self.assertContains(response, 'blocked on review')
        self.assertContains(
            response, 'data-testid="crm-slack-autoapply-change-undo"', count=2,
        )
        self.assertContains(
            response, 'data-testid="crm-slack-autoapply-undo-all"',
        )

    def test_thread_without_event_renders_no_autoapply_block(self):
        thread = SlackThread.objects.create(
            channel_id=CHANNEL, thread_ts='1700000099.000001',
            slack_user_id='U_AUTHOR', member=self.member, plan=self.plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread, ts='1700000099.000001', slack_user_id='U_AUTHOR',
            text='just chatting', posted_at=timezone.now(), is_root=True,
        )
        url = reverse('studio_plan_detail', kwargs={'plan_id': self.plan.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-slack-thread"')
        self.assertNotContains(response, 'data-testid="crm-slack-autoapply"')
