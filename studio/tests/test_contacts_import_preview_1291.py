"""Outcome-preview and stale-confirm coverage for contact imports (#1291)."""

import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from accounts.models import TierOverride
from studio.services.contacts_import import (
    NO_TIER_CHANGE,
    parse_csv,
    plan_contact_rows,
    plan_csv_import,
    run_import,
)

User = get_user_model()


def _csv(*rows):
    output = io.StringIO()
    for row in rows:
        output.write(','.join(row) + '\n')
    return output.getvalue()


def _upload(text):
    return SimpleUploadedFile('contacts.csv', text.encode(), content_type='text/csv')


class ContactImportPlanTest(TestCase):
    def test_classifier_covers_invalid_duplicate_existing_new_and_plus_address(self):
        User.objects.create_user(email='existing@example.com', password=None)
        rows = [
            {'email': ' EXISTING@example.com '},
            {'email': 'new@example.com'},
            {'email': 'NEW@example.com'},
            {'email': 'new+offer@example.com'},
            {'email': ''},
            {'email': 'foo@bar'},
        ]

        plan = plan_contact_rows(rows)

        self.assertEqual(plan.total_rows, 6)
        self.assertEqual(plan.plausible_emails, 4)
        self.assertEqual(plan.created, 2)
        self.assertEqual(plan.updated, 1)
        self.assertEqual(plan.skipped, 1)
        self.assertEqual(plan.malformed, 2)
        self.assertEqual(plan.total_skipped, 3)
        self.assertEqual(
            plan.created + plan.updated + plan.skipped + plan.malformed,
            plan.total_rows,
        )
        self.assertEqual(
            plan.warning,
            'Only 4/6 values in this column look like email addresses.',
        )

    def test_existing_lookup_is_batched_not_one_query_per_row(self):
        rows = [{'email': f'user-{index}@example.com'} for index in range(1001)]
        with self.assertNumQueries(3):
            plan = plan_contact_rows(rows, lookup_batch_size=500)
        self.assertEqual(plan.created, 1001)

    def test_preview_and_apply_share_the_same_plan_and_counts(self):
        staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        existing = User.objects.create_user(email='existing@example.com', password=None)
        parsed, error = parse_csv(_csv(
            ('Email',),
            ('existing@example.com',),
            ('new-one@example.com',),
            ('new-two@example.com',),
            ('not-an-email',),
        ))
        self.assertIsNone(error)
        plan = plan_csv_import(parsed, email_column='Email')

        result = run_import(
            parsed,
            email_column='Email',
            tag='previewed',
            tier=None,
            granted_by=staff,
            plan=plan,
        )

        self.assertEqual(
            (result.created, result.updated, result.skipped, result.malformed),
            (plan.created, plan.updated, plan.skipped, plan.malformed),
        )
        existing.refresh_from_db()
        self.assertIn('previewed', existing.tags)

    def test_planning_has_no_application_or_provider_side_effects(self):
        existing = User.objects.create_user(
            email='existing@example.com', password=None, tags=['keep'],
        )
        before_users = User.objects.count()
        with mock.patch(
            'studio.services.contacts_import.backfill_user_from_stripe',
        ) as provider:
            plan = plan_contact_rows([
                {'email': existing.email, 'stripe_customer_id': 'cus_new'},
                {'email': 'new@example.com', 'tags': ['new-tag']},
            ])

        provider.assert_not_called()
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(TierOverride.objects.count(), 0)
        existing.refresh_from_db()
        self.assertEqual(existing.tags, ['keep'])
        self.assertEqual(plan.updated, 1)
        self.assertEqual(plan.created, 1)


class ContactImportPreviewViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='pw')

    def _start(self, text):
        response = self.client.post(
            '/studio/users/import/', {'csv_file': _upload(text)},
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_initial_preview_shows_exact_mixed_outcome_and_accessible_contract(self):
        User.objects.create_user(email='existing@example.com', password=None)
        response = self._start(_csv(
            ('Name', 'Email'),
            ('Existing', 'existing@example.com'),
            ('One', 'one@example.com'),
            ('Two', 'two@example.com'),
            ('Bad', 'not-an-email'),
        ))

        self.assertEqual(response.context['preview']['created'], 2)
        self.assertEqual(response.context['preview']['updated'], 1)
        self.assertEqual(response.context['preview']['malformed'], 1)
        self.assertContains(
            response,
            '2 new users will be created, 1 existing user will be updated, '
            '1 row will be skipped (1 invalid email, 0 duplicates).',
        )
        self.assertContains(response, 'Only 3/4 values in this column look like email addresses.')
        self.assertContains(response, 'aria-describedby="email-column-help import-preview-feedback"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'data-testid="import-preview-loading"')
        self.assertContains(response, 'data-testid="import-preview-retry"')
        self.assertNotContains(response, 'data-testid="import-confirm-submit" disabled')

        stash = self.client.session['studio_user_import_payload']
        self.assertEqual(set(stash), {'raw_text', 'header', 'filename', 'preview'})
        self.assertNotIn('rows', stash)
        self.assertNotIn('existing@example.com', str(stash['preview']))

    def test_zero_plausible_initial_preview_and_direct_confirm_are_blocked(self):
        response = self._start(_csv(
            ('Name', 'Source'),
            ('Ada', 'event'),
            ('Grace', 'newsletter'),
        ))
        self.assertContains(response, 'Only 0/2 values in this column look like email addresses.')
        self.assertContains(response, 'Choose another email column to continue.')
        self.assertContains(response, 'disabled aria-disabled="true"')

        before = User.objects.count()
        confirm = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Name',
            'tag': 'must-not-land',
            'tier_id': NO_TIER_CHANGE,
        })
        self.assertEqual(confirm.status_code, 400)
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertIn('studio_user_import_payload', self.client.session)

    def test_preview_endpoint_remaps_with_aggregate_only_json_and_no_writes(self):
        self._start(_csv(
            ('Name', 'Contact email'),
            ('Ada', 'ada@example.com'),
            ('Grace', 'grace@example.com'),
        ))
        before = User.objects.count()
        response = self.client.post(
            '/studio/users/import/preview', {'email_column': 'Contact email'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'email_column': 'Contact email',
            'total_rows': 2,
            'plausible_emails': 2,
            'created': 2,
            'updated': 0,
            'skipped': 0,
            'malformed': 0,
            'total_skipped': 0,
            'can_apply': True,
            'warning': '',
        })
        self.assertEqual(User.objects.count(), before)
        self.assertNotIn('ada@example.com', response.content.decode())
        self.assertEqual(
            self.client.session['studio_user_import_payload']['preview']['email_column'],
            'Contact email',
        )

    def test_preview_endpoint_validation_expiry_permissions_and_csrf(self):
        expired = self.client.post(
            '/studio/users/import/preview', {'email_column': 'Email'},
        )
        self.assertEqual(expired.status_code, 400)
        self.assertEqual(expired.json()['code'], 'upload_session_expired')

        self._start(_csv(('Email',), ('one@example.com',)))
        unknown = self.client.post(
            '/studio/users/import/preview', {'email_column': 'Unknown'},
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()['code'], 'invalid_email_column')
        extra = self.client.post(
            '/studio/users/import/preview',
            {'email_column': 'Email', 'raw_email': 'secret@example.com'},
        )
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(extra.json()['code'], 'invalid_preview_fields')
        self.assertNotIn('secret@example.com', extra.content.decode())

        anonymous = Client().post(
            '/studio/users/import/preview', {'email_column': 'Email'},
        )
        self.assertEqual(anonymous.status_code, 302)
        member = User.objects.create_user(email='member@example.com', password='pw')
        member_client = Client()
        member_client.login(email=member.email, password='pw')
        self.assertEqual(
            member_client.post(
                '/studio/users/import/preview', {'email_column': 'Email'},
            ).status_code,
            403,
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.staff.email, password='pw')
        self.assertEqual(
            csrf_client.post(
                '/studio/users/import/preview', {'email_column': 'Email'},
            ).status_code,
            403,
        )

    def test_changed_database_outcome_requires_reconfirmation_then_applies_once(self):
        self._start(_csv(('Email',), ('race@example.com',)))
        raced = User.objects.create_user(email='race@example.com', password=None)

        first = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Email',
            'tag': 'reviewed',
            'tier_id': NO_TIER_CHANGE,
        })
        self.assertEqual(first.status_code, 409)
        self.assertContains(
            first,
            'Import outcome changed. Review the updated counts and confirm again.',
            status_code=409,
        )
        raced.refresh_from_db()
        self.assertEqual(raced.tags, [])

        second = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Email',
            'tag': 'reviewed',
            'tier_id': NO_TIER_CHANGE,
        })
        self.assertEqual(second.status_code, 200)
        raced.refresh_from_db()
        self.assertEqual(raced.tags, ['reviewed'])
        self.assertNotIn('studio_user_import_payload', self.client.session)
