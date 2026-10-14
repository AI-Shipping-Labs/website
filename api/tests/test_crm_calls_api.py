"""Tests for ``POST /api/crm/calls`` and the export aggregate calls.

Issue #1949 AC14-AC15: staff-token write endpoint with the same
matching rules as ``record_call`` (unknown email records with
``contact=NULL``), 201 on success, 401 for non-staff tokens, 405 for
other methods; ``GET /api/crm/export`` includes per-user ``calls``.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from api.views.crm_export import build_single_crm_record_aggregate
from crm.models import CRMRecord
from crm.models.call import Call
from crm.services.calls import record_call

User = get_user_model()


def _iso(minute=0):
    return f'2026-10-13T15:{minute:02d}:00Z'


class CrmCallsEndpointBase(TestCase):
    URL = '/api/crm/calls'

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='calls-staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='ops')

        # A token whose owner was staff at creation then demoted --
        # ``token_required`` re-checks ``is_staff`` at request time.
        cls.nonstaff = User.objects.create_user(
            email='calls-demoted@test.com', password='pw', is_staff=True,
        )
        cls.nonstaff_token = Token.objects.create(user=cls.nonstaff, name='m')
        cls.nonstaff.is_staff = False
        cls.nonstaff.save(update_fields=['is_staff'])

        cls.member = User.objects.create_user(
            email='calls-member@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(
            user=cls.member, created_by=cls.staff,
        )

    def _post(self, payload, token='staff'):
        kwargs = {'data': json.dumps(payload), 'content_type': 'application/json'}
        if token == 'staff':
            kwargs['HTTP_AUTHORIZATION'] = f'Token {self.staff_token.key}'
        elif token == 'nonstaff':
            kwargs['HTTP_AUTHORIZATION'] = f'Token {self.nonstaff_token.key}'
        return self.client.post(self.URL, **kwargs)


class CrmCallsCreateTest(CrmCallsEndpointBase):
    def test_creates_call_for_tracked_contact(self):
        response = self._post({
            'email': self.member.email,
            'occurred_at': _iso(),
            'summary': 'Weekly sync about the portfolio project.',
            'source': 'manual',
        })
        self.assertEqual(response.status_code, 201)
        call = response.json()
        self.assertEqual(call['email'], self.member.email)
        self.assertEqual(call['contact_id'], self.record.pk)
        self.assertEqual(call['source'], 'manual')
        self.assertIn('Weekly sync', call['summary'])
        self.assertTrue(
            Call.objects.filter(email=self.member.email).exists(),
        )

    def test_unknown_email_records_with_null_contact(self):
        response = self._post({
            'email': 'stranger@elsewhere.org',
            'occurred_at': _iso(minute=5),
            'summary': 'Cold call',
        })
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()['contact_id'])

    def test_source_ref_upserts_instead_of_duplicating(self):
        first = self._post({
            'email': self.member.email,
            'occurred_at': _iso(minute=10),
            'source': 'booking',
            'source_ref': 'BK-API-1',
        })
        second = self._post({
            'email': self.member.email,
            'occurred_at': _iso(minute=11),
            'source': 'booking',
            'source_ref': 'BK-API-1',
        })
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(
            second.json()['id'],
            first.json()['id'],
        )
        self.assertEqual(Call.objects.filter(source='booking').count(), 1)

    def test_zoom_source_is_accepted(self):
        # Issue #1951 AC1: the import writes with source=zoom.
        response = self._post({
            'email': self.member.email,
            'occurred_at': _iso(minute=25),
            'summary': 'Historical 1:1 summary.',
            'source': 'zoom',
            'source_ref': '2026-04-23-jane-doe-summary',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['source'], 'zoom')
        self.assertTrue(
            Call.objects.filter(
                source='zoom',
                source_ref='2026-04-23-jane-doe-summary',
            ).exists(),
        )

    def test_unknown_source_returns_422(self):
        response = self._post({
            'email': self.member.email,
            'occurred_at': _iso(minute=30),
            'source': 'carrier-pigeon',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['details']['field'], 'source')
        self.assertEqual(response.json()['details']['value'], 'carrier-pigeon')
        self.assertEqual(
            Call.objects.filter(source='carrier-pigeon').count(),
            0,
        )

    def test_missing_email_returns_422(self):
        response = self._post({'occurred_at': _iso(minute=15)})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()['details']['field'], 'email',
        )

    def test_invalid_occurred_at_returns_422(self):
        response = self._post({
            'email': self.member.email,
            'occurred_at': 'not-a-date',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()['details']['field'], 'occurred_at',
        )

    def test_requires_token(self):
        response = self.client.post(
            self.URL, data='{}', content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_rejects_non_staff_token(self):
        response = self._post(
            {'email': self.member.email, 'occurred_at': _iso(minute=20)},
            token='nonstaff',
        )
        self.assertEqual(response.status_code, 401)

    def test_rejects_get(self):
        response = self.client.get(
            self.URL, HTTP_AUTHORIZATION=f'Token {self.staff_token.key}',
        )
        self.assertEqual(response.status_code, 405)


class CrmExportCallsAggregateTest(CrmCallsEndpointBase):
    def test_export_aggregate_includes_calls(self):
        call = record_call(
            email=self.member.email,
            occurred_at=timezone.make_aware(
                datetime.datetime(2026, 10, 13, 15, 30),
            ),
            summary='Export aggregate call',
            source='booking',
            source_ref='BK-EXPORT-1',
        )
        aggregate = build_single_crm_record_aggregate(
            self.record, bearer=self.staff,
        )
        self.assertIn('calls', aggregate)
        entries = [
            row for row in aggregate['calls'] if row['id'] == call.pk
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['summary'], 'Export aggregate call')
        self.assertEqual(entries[0]['source'], 'booking')
        self.assertEqual(entries[0]['source_ref'], 'BK-EXPORT-1')
