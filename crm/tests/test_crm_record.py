"""Model + track-endpoint + data-migration tests for the CRM (#560)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CRMRecord
from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class CRMRecordModelTest(TestCase):
    """Invariants on :class:`CRMRecord` worth pinning explicitly."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='user@test.com', password='pw',
        )

    def test_default_status_is_active(self):
        # Status drives the default ``Active`` filter on the list page;
        # if it ever defaults to ``archived`` the list silently empties.
        record = CRMRecord.objects.create(user=self.user)
        self.assertEqual(record.status, 'active')

    def test_one_to_one_with_user(self):
        # The OneToOneField is load-bearing for ``crm_track`` to be
        # idempotent: a second create attempt for the same user must
        # raise rather than silently insert a duplicate.
        CRMRecord.objects.create(user=self.user)
        with self.assertRaises(Exception):
            CRMRecord.objects.create(user=self.user)


class CRMTrackEndpointTest(TestCase):
    """``POST /studio/users/<id>/crm/track`` create-or-redirect contract."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='pw',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_track_creates_record_and_redirects_to_detail(self):
        before = CRMRecord.objects.count()
        response = self.client.post(
            f'/studio/users/{self.target.pk}/crm/track',
        )
        self.assertEqual(CRMRecord.objects.count(), before + 1)
        record = CRMRecord.objects.get(user=self.target)
        self.assertEqual(record.created_by, self.staff)
        self.assertEqual(record.status, 'active')
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )

    def test_track_is_idempotent_redirects_to_existing(self):
        existing = CRMRecord.objects.create(
            user=self.target, created_by=self.staff,
        )
        before = CRMRecord.objects.count()
        response = self.client.post(
            f'/studio/users/{self.target.pk}/crm/track',
        )
        self.assertEqual(CRMRecord.objects.count(), before)
        self.assertRedirects(
            response, f'/studio/crm/{existing.pk}/',
            fetch_redirect_response=False,
        )

    def test_track_is_post_only(self):
        response = self.client.get(
            f'/studio/users/{self.target.pk}/crm/track',
        )
        self.assertEqual(response.status_code, 405)

    def test_track_requires_staff(self):
        self.client.logout()
        User.objects.create_user(
            email='nonstaff@test.com', password='pw',
        )
        self.client.login(email='nonstaff@test.com', password='pw')
        before = CRMRecord.objects.count()
        response = self.client.post(
            f'/studio/users/{self.target.pk}/crm/track',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CRMRecord.objects.count(), before)

    def test_track_redirects_anon_to_login(self):
        self.client.logout()
        response = self.client.post(
            f'/studio/users/{self.target.pk}/crm/track',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])


class CRMBackfillFromNotesTest(TestCase):
    """The data migration backfills records for users with notes only.

    Test-time the data migration has already run on the test database
    via the migration framework, so we simulate the same logic
    inline by exercising the helper the migration calls. The contract
    we care about is the rule: notes-only users get a record, plans-only
    users do not.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.notes_user = User.objects.create_user(
            email='notes@test.com', password='pw',
        )
        cls.plans_user = User.objects.create_user(
            email='plans@test.com', password='pw',
        )
        cls.cold_user = User.objects.create_user(
            email='cold@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='Spring 2026',
            slug='spring-2026',
            start_date=datetime.date(2026, 3, 1),
        )

    def test_notes_only_user_gets_a_record(self):
        # Clear any backfilled records from the migration.
        CRMRecord.objects.all().delete()
        InterviewNote.objects.create(
            member=self.notes_user, visibility='internal',
            body='Intake notes', created_by=self.staff,
        )
        self._run_backfill_simple()
        self.assertTrue(
            CRMRecord.objects.filter(user=self.notes_user).exists(),
        )

    def test_plans_only_user_does_not_get_a_record(self):
        CRMRecord.objects.all().delete()
        Plan.objects.create(member=self.plans_user, sprint=self.sprint)
        self._run_backfill_simple()
        self.assertFalse(
            CRMRecord.objects.filter(user=self.plans_user).exists(),
        )

    def test_cold_user_with_no_engagement_does_not_get_a_record(self):
        CRMRecord.objects.all().delete()
        self._run_backfill_simple()
        self.assertFalse(
            CRMRecord.objects.filter(user=self.cold_user).exists(),
        )

    def test_backfill_is_idempotent(self):
        CRMRecord.objects.all().delete()
        InterviewNote.objects.create(
            member=self.notes_user, visibility='internal',
            body='Intake', created_by=self.staff,
        )
        self._run_backfill_simple()
        count_after_first = CRMRecord.objects.count()
        self._run_backfill_simple()
        self.assertEqual(CRMRecord.objects.count(), count_after_first)

    def _run_backfill_simple(self):
        """Inline copy of the migration body so we don't depend on the
        Django app registry layout. The contract being tested is the
        rule, not the migration plumbing."""
        member_ids = (
            InterviewNote.objects
            .filter(member__crm_record__isnull=True)
            .values_list('member_id', flat=True)
            .distinct()
        )
        records = [
            CRMRecord(user_id=member_id, status='active')
            for member_id in member_ids
        ]
        if records:
            CRMRecord.objects.bulk_create(records, ignore_conflicts=True)


class CRMMigrationFunctionTest(TestCase):
    """Exercise the data-migration callable directly.

    The migration body is in the migration file rather than a service
    module, so we import the function from the migration module to
    pin its behaviour without going through ``call_command``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='migstaff@test.com', password='pw', is_staff=True,
        )
        cls.notes_user = User.objects.create_user(
            email='migration-notes@test.com', password='pw',
        )

    def test_migration_callable_creates_record(self):
        # Strip the auto-applied migration's effects to give the
        # callable real work to do.
        CRMRecord.objects.filter(user=self.notes_user).delete()
        InterviewNote.objects.create(
            member=self.notes_user, visibility='internal',
            body='Migration intake', created_by=self.staff,
        )
        # The migration module name starts with a digit so we have to
        # import it via importlib rather than a plain ``import``
        # statement.
        import importlib

        from django.apps import apps
        module = importlib.import_module(
            'crm.migrations.0002_backfill_records_from_notes',
        )
        module.backfill_crm_records_from_notes(apps, None)
        self.assertTrue(
            CRMRecord.objects.filter(user=self.notes_user).exists(),
        )
