"""Tests for ``crm.services.calls.record_call`` (issue #1949, AC4).

The service is the booking app's integration point, so matching
(case-insensitive email, contact linked only for already-tracked
members, never auto-creating a CRM record) and ``(source, source_ref)``
idempotency are the critical behaviors covered here.
"""

import datetime
import importlib
import sys

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crm.models import CRMRecord
from crm.models.call import Call
from crm.services.calls import calls_for_member, record_call

User = get_user_model()


def _when(minute=0):
    return timezone.make_aware(datetime.datetime(2026, 10, 13, 15, minute))


class RecordCallServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='svc-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='svc-member@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(
            user=cls.member, created_by=cls.staff,
        )
        # A known user who was never tracked in the CRM.
        cls.untracked = User.objects.create_user(
            email='svc-untracked@test.com', password='pw',
        )

    def test_creates_manual_call_linked_to_contact(self):
        call = record_call(
            email=self.member.email,
            occurred_at=_when(),
            summary='Intro call',
            created_by=self.staff,
        )
        self.assertEqual(call.contact, self.record)
        self.assertEqual(call.email, self.member.email)
        self.assertEqual(call.summary, 'Intro call')
        self.assertEqual(call.source, 'manual')
        self.assertEqual(call.source_ref, '')
        self.assertEqual(call.created_by, self.staff)

    def test_email_matching_is_case_insensitive(self):
        call = record_call(
            email='  Svc-Member@Test.COM ',
            occurred_at=_when(minute=5),
        )
        self.assertEqual(call.contact, self.record)
        self.assertEqual(call.email, 'Svc-Member@Test.COM')

    def test_unknown_email_records_with_null_contact(self):
        call = record_call(
            email='stranger@elsewhere.org',
            occurred_at=_when(minute=10),
            summary='Inbound sales call',
            source='booking',
            source_ref='BK-UNKNOWN-1',
        )
        self.assertIsNone(call.contact)
        self.assertEqual(call.email, 'stranger@elsewhere.org')

    def test_known_user_without_crm_record_is_not_auto_tracked(self):
        call = record_call(
            email=self.untracked.email,
            occurred_at=_when(minute=15),
        )
        self.assertIsNone(call.contact)
        self.assertFalse(
            CRMRecord.objects.filter(user=self.untracked).exists(),
        )

    def test_source_ref_upserts_on_source_and_ref(self):
        first = record_call(
            email=self.member.email,
            occurred_at=_when(minute=20),
            summary='Booked onboarding',
            source='booking',
            source_ref='BK-1',
        )
        second = record_call(
            email=self.member.email,
            occurred_at=_when(minute=25),
            summary='Booked onboarding (rescheduled)',
            source='booking',
            source_ref='BK-1',
        )
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(Call.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.summary, 'Booked onboarding (rescheduled)')

    def test_same_ref_under_different_source_is_a_separate_row(self):
        record_call(
            email=self.member.email, occurred_at=_when(minute=30),
            source='booking', source_ref='BK-2',
        )
        record_call(
            email=self.member.email, occurred_at=_when(minute=31),
            source='calendly', source_ref='BK-2',
        )
        self.assertEqual(Call.objects.count(), 2)

    def test_blank_source_ref_always_creates(self):
        record_call(email=self.member.email, occurred_at=_when(minute=40))
        record_call(email=self.member.email, occurred_at=_when(minute=41))
        self.assertEqual(
            Call.objects.filter(source='manual').count(), 2,
        )

    def test_import_safe_without_any_booking_app(self):
        # Remove the whole ``booking`` package family for the probe and
        # restore the exact module objects afterwards. Popping only the
        # parent (or leaving entries popped) orphans ``booking.models``
        # / ``booking.migrations`` submodules in ``sys.modules``, which
        # breaks every later MigrationExecutor reload in this process
        # (the lease-migration probe reloads them under parallel
        # sharding). Restoring keeps process-global state intact.
        booking_keys = [
            name for name in list(sys.modules)
            if name == 'booking' or name.startswith('booking.')
        ]
        probe_keys = booking_keys + ['crm.services.calls']
        saved = {
            name: sys.modules[name]
            for name in probe_keys if name in sys.modules
        }
        self.addCleanup(sys.modules.update, saved)
        for name in probe_keys:
            sys.modules.pop(name, None)

        module = importlib.import_module('crm.services.calls')
        self.assertTrue(callable(module.record_call))
        self.assertNotIn('booking', sys.modules)


class CallsForMemberTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='cfm-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='cfm-member@test.com', password='pw',
        )

    def test_surfaces_contact_linked_and_pre_tracking_email_calls(self):
        untracked_member = User.objects.create_user(
            email='cfm-late@test.com', password='pw',
        )
        pre_tracking = record_call(
            email=untracked_member.email,
            occurred_at=_when(minute=1),
            summary='Before tracking',
        )
        self.assertIsNone(pre_tracking.contact)
        late_record = CRMRecord.objects.create(
            user=untracked_member, created_by=self.staff,
        )
        tracked = record_call(
            email=untracked_member.email,
            occurred_at=_when(minute=2),
            summary='After tracking',
        )
        foreign = record_call(
            email='cfm-foreign@test.com',
            occurred_at=_when(minute=3),
            summary='Another member call',
        )

        calls = list(calls_for_member(untracked_member))
        self.assertEqual(
            [call.pk for call in calls],
            [tracked.pk, pre_tracking.pk],
        )
        self.assertNotIn(foreign.pk, {call.pk for call in calls})
        self.assertEqual(late_record.calls.count(), 2)
