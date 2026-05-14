"""Tests for the Studio contacts CSV importer (issue #356).

Two layers:

1. Service tests against ``studio.services.contacts_import`` -- exercise the
   parser + upsert logic without going through the request/response pipeline.
2. View tests against ``/studio/users/import/`` and
   ``/studio/users/import/confirm`` -- exercise file-size / file-type
   validation, session stash handoff, and the result page wiring.
"""

import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
from studio.services.contacts_import import (
    MAX_UPLOAD_BYTES,
    NO_TIER_CHANGE,
    decode_csv_bytes,
    default_email_column,
    parse_csv,
    run_import,
)

User = get_user_model()


def _build_csv(*rows):
    """Tiny helper: build a CSV string from header + data rows."""
    buffer = io.StringIO()
    for row in rows:
        buffer.write(','.join(row) + '\n')
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Service-layer tests (no HTTP)
# ---------------------------------------------------------------------------


class ParseCsvTest(TestCase):
    """``parse_csv`` returns header + rows or a user-facing error string."""

    def test_parses_header_and_rows(self):
        text = _build_csv(
            ('Name', 'Email', 'Source'),
            ('Ada', 'ada@example.com', 'event-2026'),
            ('Grace', 'grace@example.com', 'event-2026'),
        )
        parsed, error = parse_csv(text)
        self.assertIsNone(error)
        self.assertEqual(parsed.header, ['Name', 'Email', 'Source'])
        self.assertEqual(len(parsed.rows), 2)
        self.assertEqual(parsed.rows[0]['Email'], 'ada@example.com')

    def test_rejects_empty_input(self):
        parsed, error = parse_csv('')
        self.assertIsNone(parsed)
        self.assertIn('CSV is empty', error)

    def test_rejects_header_only(self):
        text = _build_csv(('Email',))
        parsed, error = parse_csv(text)
        self.assertIsNone(parsed)
        self.assertIn('CSV is empty', error)


class DefaultEmailColumnTest(TestCase):
    """``default_email_column`` finds the column literally named 'email'."""

    def test_picks_email_column_case_insensitive(self):
        self.assertEqual(default_email_column(['Name', 'Email', 'Source']), 1)
        self.assertEqual(default_email_column(['name', 'EMAIL']), 1)
        self.assertEqual(default_email_column(['  Email  ', 'name']), 0)

    def test_falls_back_to_first_column_when_no_match(self):
        self.assertEqual(default_email_column(['Address', 'Name']), 0)


class DecodeCsvBytesTest(TestCase):
    """``decode_csv_bytes`` falls back to latin-1 when UTF-8 decoding fails."""

    def test_decodes_utf8(self):
        self.assertEqual(decode_csv_bytes('hello'.encode('utf-8')), 'hello')

    def test_falls_back_to_latin1_on_invalid_utf8(self):
        # 0xff is invalid as the start byte of a UTF-8 sequence.
        text = decode_csv_bytes(b'name,email\nada,\xff@example.com')
        self.assertIn('ada', text)
        # Result should be a string, not raise.
        self.assertIsInstance(text, str)


class RunImportTest(TestCase):
    """End-to-end exercise of ``run_import`` -- the testable unit of the feature."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='x', is_staff=True,
        )
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')

    def _parse(self, text):
        parsed, error = parse_csv(text)
        self.assertIsNone(error, msg=f'parse_csv returned error: {error}')
        return parsed

    def test_creates_new_user_with_unusable_password_and_flags(self):
        parsed = self._parse(_build_csv(
            ('email',),
            ('new1@test.com',),
        ))
        result = run_import(
            parsed, email_column='email', tag='', tier=None,
            granted_by=self.staff,
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 0)
        user = User.objects.get(email='new1@test.com')
        self.assertFalse(user.email_verified)
        self.assertFalse(user.unsubscribed)
        # ``password=None`` in create_user forces an unusable password.
        self.assertFalse(user.has_usable_password())

    def test_updates_existing_user_appends_tag_without_replacing(self):
        existing = User.objects.create_user(
            email='ada@test.com', password='x', tags=['existing-tag'],
        )
        parsed = self._parse(_build_csv(
            ('email',),
            ('ada@test.com',),
        ))
        result = run_import(
            parsed, email_column='email', tag='event-2026', tier=None,
            granted_by=self.staff,
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.created, 0)
        existing.refresh_from_db()
        # The original tag is preserved; the new tag is appended.
        self.assertEqual(existing.tags, ['existing-tag', 'event-2026'])

    def test_tag_is_idempotent_on_existing_user(self):
        existing = User.objects.create_user(
            email='ada@test.com', password='x', tags=['event-2026'],
        )
        parsed = self._parse(_build_csv(
            ('email',),
            ('ada@test.com',),
        ))
        run_import(
            parsed, email_column='email', tag='Event 2026', tier=None,
            granted_by=self.staff,
        )
        existing.refresh_from_db()
        # No duplication of the normalized tag.
        self.assertEqual(existing.tags, ['event-2026'])

    def test_malformed_email_is_skipped_with_warning_naming_row(self):
        # Two distinct malformed values: one with no ``@``, one that fails
        # ``validate_email`` despite having an ``@``. Wholly-blank lines are
        # treated as noise by the parser and never reach the validator.
        parsed = self._parse(_build_csv(
            ('email',),
            ('valid@test.com',),
            ('not-an-email',),
            ('foo@bar',),
        ))
        result = run_import(
            parsed, email_column='email', tag='', tier=None,
            granted_by=self.staff,
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(result.malformed, 2)
        # Warnings name the 1-indexed row (header is row 1).
        rows = [w[0] for w in result.warnings]
        self.assertIn(3, rows)  # 'not-an-email'
        self.assertIn(4, rows)  # 'foo@bar'
        # Other valid rows still imported.
        self.assertTrue(User.objects.filter(email='valid@test.com').exists())

    def test_validate_email_rejects_obviously_bad_addresses(self):
        # Validate that the django ``validate_email`` validator catches
        # cases beyond the simple "@ missing" check (e.g. trailing dot).
        parsed = self._parse(_build_csv(
            ('email',),
            ('foo@bar',),  # no TLD -- validate_email rejects this
        ))
        result = run_import(
            parsed, email_column='email', tag='', tier=None,
            granted_by=self.staff,
        )
        self.assertEqual(result.malformed, 1)
        self.assertEqual(result.created, 0)

    def test_duplicate_within_file_counts_as_skipped(self):
        parsed = self._parse(_build_csv(
            ('email',),
            ('dup@test.com',),
            ('DUP@test.com',),
        ))
        result = run_import(
            parsed, email_column='email', tag='', tier=None,
            granted_by=self.staff,
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 1)
        # The skipped row shows up in warnings with the correct reason.
        reasons = [w[2] for w in result.warnings]
        self.assertIn('duplicate within file', reasons)

    def test_tier_override_created_with_long_expiry(self):
        parsed = self._parse(_build_csv(
            ('email',),
            ('upgrade@test.com',),
        ))
        result = run_import(
            parsed, email_column='email', tag='', tier=self.main_tier,
            granted_by=self.staff,
        )
        self.assertEqual(result.created, 1)
        user = User.objects.get(email='upgrade@test.com')
        override = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(override.override_tier, self.main_tier)
        self.assertEqual(override.granted_by, self.staff)
        # ~10 years out -- tolerate any drift below 9 years.
        delta = override.expires_at - timezone.now()
        self.assertGreater(delta.days, 9 * 365)

    def test_studio_csv_import_still_creates_tier_override_for_paid_tier(self):
        """Issue #636: API path skips overrides, but Studio CSV path must keep
        creating long-lived overrides for paid tiers picked from the dropdown.

        ``run_import`` calls ``import_contact_rows`` with the default
        ``tier_assignment_mode='override'``, so the override branch fires
        regardless of whether the user has a Stripe customer ID. This
        regression-locks the Studio operator workflow against the API change.
        """
        parsed = self._parse(_build_csv(
            ('email',),
            ('studio-override@test.com',),
        ))
        result = run_import(
            parsed, email_column='email', tag='', tier=self.main_tier,
            granted_by=self.staff,
        )
        self.assertEqual(result.created, 1)
        user = User.objects.get(email='studio-override@test.com')
        # Stripe is never consulted from the Studio path (the user has no
        # stripe_customer_id) and the long-lived override IS created.
        self.assertEqual(user.stripe_customer_id, '')
        override = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(override.override_tier, self.main_tier)
        self.assertEqual(override.granted_by, self.staff)
        # The override duration matches _apply_tier_override's ~10y constant.
        delta = override.expires_at - timezone.now()
        self.assertGreater(delta.days, 9 * 365)

    def test_tier_override_deactivates_existing_active_override(self):
        existing = User.objects.create_user(email='upg@test.com', password='x')
        prior = TierOverride.objects.create(
            user=existing,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timezone.timedelta(days=30),
            granted_by=self.staff,
            is_active=True,
        )
        parsed = self._parse(_build_csv(
            ('email',),
            ('upg@test.com',),
        ))
        run_import(
            parsed, email_column='email', tag='', tier=self.main_tier,
            granted_by=self.staff,
        )
        prior.refresh_from_db()
        self.assertFalse(prior.is_active)
        active = TierOverride.objects.filter(user=existing, is_active=True)
        self.assertEqual(active.count(), 1)

    def test_no_tier_change_creates_no_override(self):
        existing = User.objects.create_user(email='nochange@test.com', password='x')
        parsed = self._parse(_build_csv(
            ('email',),
            ('nochange@test.com',),
        ))
        run_import(
            parsed, email_column='email', tag='', tier=None,
            granted_by=self.staff,
        )
        self.assertFalse(TierOverride.objects.filter(user=existing).exists())

    def test_free_tier_is_no_op_for_override(self):
        parsed = self._parse(_build_csv(
            ('email',),
            ('freebie@test.com',),
        ))
        run_import(
            parsed, email_column='email', tag='', tier=self.free_tier,
            granted_by=self.staff,
        )
        user = User.objects.get(email='freebie@test.com')
        # Free tier is level 0; we don't create an override that wouldn't
        # grant any new access.
        self.assertFalse(TierOverride.objects.filter(user=user).exists())

    def test_atomic_rollback_on_mid_import_error(self):
        """If an unexpected error happens mid-import, all rows roll back."""
        parsed = self._parse(_build_csv(
            ('email',),
            ('first@test.com',),
            ('second@test.com',),
        ))
        # Patch ``_apply_tier_override`` to raise on the second call so the
        # whole transaction must roll back.
        call_counter = {'count': 0}

        def _boom(user, override_tier, granted_by):
            call_counter['count'] += 1
            if call_counter['count'] == 2:
                raise RuntimeError('simulated mid-import failure')
            from accounts.models import TierOverride as _TO
            _TO.objects.create(
                user=user, override_tier=override_tier,
                expires_at=timezone.now() + timezone.timedelta(days=1),
                granted_by=granted_by, is_active=True,
            )

        with mock.patch(
            'studio.services.contacts_import._apply_tier_override',
            side_effect=_boom,
        ):
            with self.assertRaises(RuntimeError):
                run_import(
                    parsed, email_column='email', tag='',
                    tier=self.main_tier, granted_by=self.staff,
                )

        # Neither user was committed, and no override survived.
        self.assertFalse(User.objects.filter(email='first@test.com').exists())
        self.assertFalse(User.objects.filter(email='second@test.com').exists())
        self.assertEqual(TierOverride.objects.count(), 0)


# ---------------------------------------------------------------------------
# View-layer tests (HTTP)
# ---------------------------------------------------------------------------


def _csv_upload(text, name='contacts.csv', content_type='text/csv'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type=content_type)


class UserImportUploadViewTest(TestCase):
    """``GET`` and ``POST`` for ``/studio/users/import/`` (step 1+2)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_get_returns_upload_form(self):
        response = self.client.get('/studio/users/import/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/import.html')
        self.assertContains(response, 'data-testid="import-upload-form"')

    def test_get_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(email='reg@test.com', password='x', is_staff=False)
        self.client.login(email='reg@test.com', password='x')
        response = self.client.get('/studio/users/import/')
        self.assertEqual(response.status_code, 403)

    def test_post_non_csv_returns_error_and_does_not_stash(self):
        upload = _csv_upload('not a csv', name='evil.exe', content_type='application/octet-stream')
        response = self.client.post('/studio/users/import/', {'csv_file': upload})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Only .csv files are supported.', status_code=400)
        # No session stash was written.
        self.assertNotIn('studio_user_import_payload', self.client.session)

    def test_post_oversized_csv_rejected(self):
        # Build a CSV string slightly larger than MAX_UPLOAD_BYTES. The
        # SimpleUploadedFile.size is the byte length we pass in.
        big_text = 'email\n' + ('a@test.com\n' * (MAX_UPLOAD_BYTES // 11 + 100))
        upload = _csv_upload(big_text, name='big.csv', content_type='text/csv')
        response = self.client.post('/studio/users/import/', {'csv_file': upload})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'File too large (max 5 MB).', status_code=400)
        self.assertNotIn('studio_user_import_payload', self.client.session)

    def test_post_valid_csv_renders_confirm_page_with_preview(self):
        text = _build_csv(
            ('Name', 'Email', 'Source'),
            ('Ada', 'ada@test.com', 'event-2026'),
            ('Grace', 'grace@test.com', 'event-2026'),
        )
        upload = _csv_upload(text)
        response = self.client.post('/studio/users/import/', {'csv_file': upload})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/import_confirm.html')
        self.assertEqual(response.context['header'], ['Name', 'Email', 'Source'])
        # Default email column is the column literally named 'Email'.
        self.assertEqual(response.context['default_email_column'], 'Email')
        self.assertEqual(len(response.context['preview_rows']), 2)
        # Stash was written.
        self.assertIn('studio_user_import_payload', self.client.session)

    def test_preview_caps_at_five_rows(self):
        rows = [('Email',)] + [(f'u{i}@test.com',) for i in range(10)]
        text = _build_csv(*rows)
        upload = _csv_upload(text)
        response = self.client.post('/studio/users/import/', {'csv_file': upload})
        self.assertEqual(len(response.context['preview_rows']), 5)
        self.assertEqual(response.context['total_rows'], 10)

    def test_post_empty_csv_returns_error(self):
        upload = _csv_upload('', name='empty.csv')
        response = self.client.post('/studio/users/import/', {'csv_file': upload})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'CSV is empty', status_code=400)


class UserImportConfirmViewTest(TestCase):
    """``POST /studio/users/import/confirm`` -- step 3."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.main_tier = Tier.objects.get(slug='main')

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _stash_payload(self, text):
        """Round-trip the upload step so the session stash is set."""
        upload = _csv_upload(text)
        response = self.client.post('/studio/users/import/', {'csv_file': upload})
        self.assertEqual(response.status_code, 200, msg='upload step failed')

    def test_confirm_without_stash_redirects(self):
        response = self.client.post('/studio/users/import/confirm', {})
        self.assertRedirects(response, '/studio/users/import/')

    def test_confirm_runs_import_and_renders_result(self):
        text = _build_csv(
            ('Email',),
            ('new1@test.com',),
            ('not-an-email',),
        )
        self._stash_payload(text)
        response = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Email',
            'tag': 'event-2026',
            'tier_id': str(self.main_tier.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/import_result.html')
        self.assertEqual(response.context['created'], 1)
        self.assertEqual(response.context['malformed'], 1)
        self.assertEqual(response.context['tag'], 'event-2026')
        self.assertEqual(response.context['tier_name'], 'Main')
        # New user exists with the tag and the override.
        user = User.objects.get(email='new1@test.com')
        self.assertEqual(user.tags, ['event-2026'])
        self.assertTrue(
            TierOverride.objects.filter(user=user, is_active=True).exists()
        )
        # Stash was cleared.
        self.assertNotIn('studio_user_import_payload', self.client.session)

    def test_confirm_with_no_tier_change_creates_no_overrides(self):
        text = _build_csv(('Email',), ('a@test.com',))
        self._stash_payload(text)
        response = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Email',
            'tag': '',
            'tier_id': NO_TIER_CHANGE,
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email='a@test.com')
        self.assertFalse(TierOverride.objects.filter(user=user).exists())

    def test_confirm_rejects_unknown_email_column(self):
        text = _build_csv(('Email',), ('a@test.com',))
        self._stash_payload(text)
        response = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Bogus',
            'tier_id': NO_TIER_CHANGE,
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'Pick which column holds the email', status_code=400,
        )
        self.assertFalse(User.objects.filter(email='a@test.com').exists())

    def test_confirm_rejects_tag_that_normalizes_to_empty(self):
        text = _build_csv(('Email',), ('a@test.com',))
        self._stash_payload(text)
        response = self.client.post('/studio/users/import/confirm', {
            'email_column': 'Email',
            'tag': '!!!',  # normalizes to empty string
            'tier_id': NO_TIER_CHANGE,
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'Tag normalized to an empty string', status_code=400,
        )
        # Import did not run.
        self.assertFalse(User.objects.filter(email='a@test.com').exists())


class UserListImportLinkTest(TestCase):
    """The list-page header carries an ``Import contacts`` link."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def test_users_list_renders_import_link(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="user-import-link"')
        self.assertContains(response, '/studio/users/import/')
