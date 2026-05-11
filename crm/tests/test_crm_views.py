"""Studio CRM view tests (issue #560).

Covers the list, detail, edit (snapshot), archive/reactivate, and
experiments CRUD endpoints, plus staff-only access control.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CRMExperiment, CRMRecord
from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class CRMViewsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
            first_name='Mem', last_name='Ber',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='Spring 2026',
            slug='spring-2026',
            start_date=datetime.date(2026, 3, 1),
        )

    def setUp(self):
        # ``setUpTestData`` runs once per class; each test starts with
        # a fresh CRM table because the test transaction wraps every
        # method. We still wipe to guard against any global side
        # effects from the data migration.
        CRMRecord.objects.all().delete()
        self.client.login(email='staff@test.com', password='pw')


class CRMListViewTest(CRMViewsBase):
    def test_empty_state(self):
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-empty-state"')
        self.assertContains(
            response,
            'No CRM records yet. Open a user profile and click',
        )

    def test_list_shows_record_with_counts_and_links(self):
        record = CRMRecord.objects.create(
            user=self.member, created_by=self.staff, persona='Sam',
        )
        Plan.objects.create(member=self.member, sprint=self.sprint)
        InterviewNote.objects.create(
            member=self.member, visibility='internal',
            body='note 1', created_by=self.staff,
        )
        InterviewNote.objects.create(
            member=self.member, visibility='external',
            body='note 2', created_by=self.staff,
        )
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-testid="crm-row-{record.pk}"')
        self.assertContains(response, 'member@test.com')
        self.assertContains(response, 'Sam')
        self.assertContains(response, f'href="/studio/crm/{record.pk}/"')
        self.assertContains(
            response,
            f'href="/studio/users/{self.member.pk}/"',
        )
        # Plans count column shows 1, notes count column shows 2.
        self.assertContains(
            response,
            'data-testid="crm-row-plans-count">1',
        )
        self.assertContains(
            response,
            'data-testid="crm-row-notes-count">2',
        )

    def test_default_filter_is_active_and_hides_archived(self):
        active_record = CRMRecord.objects.create(
            user=self.member, status='active',
        )
        archived_record = CRMRecord.objects.create(
            user=self.other, status='archived',
        )
        response = self.client.get('/studio/crm/')
        self.assertContains(response, f'data-testid="crm-row-{active_record.pk}"')
        self.assertNotContains(response, f'data-testid="crm-row-{archived_record.pk}"')

    def test_archived_filter_shows_only_archived(self):
        active_record = CRMRecord.objects.create(
            user=self.member, status='active',
        )
        archived_record = CRMRecord.objects.create(
            user=self.other, status='archived',
        )
        response = self.client.get('/studio/crm/?filter=archived')
        self.assertNotContains(response, f'data-testid="crm-row-{active_record.pk}"')
        self.assertContains(response, f'data-testid="crm-row-{archived_record.pk}"')

    def test_all_filter_shows_both(self):
        active_record = CRMRecord.objects.create(
            user=self.member, status='active',
        )
        archived_record = CRMRecord.objects.create(
            user=self.other, status='archived',
        )
        response = self.client.get('/studio/crm/?filter=all')
        self.assertContains(response, f'data-testid="crm-row-{active_record.pk}"')
        self.assertContains(response, f'data-testid="crm-row-{archived_record.pk}"')

    def test_search_filters_by_email_substring(self):
        CRMRecord.objects.create(user=self.member)
        CRMRecord.objects.create(user=self.other)
        response = self.client.get('/studio/crm/?q=other')
        self.assertNotContains(response, 'member@test.com')
        self.assertContains(response, 'other@test.com')

    def test_search_filters_by_persona_substring(self):
        rec1 = CRMRecord.objects.create(user=self.member, persona='Sam — Technical')
        CRMRecord.objects.create(user=self.other, persona='Alex — Marketing')
        response = self.client.get('/studio/crm/?q=technical')
        self.assertContains(response, f'data-testid="crm-row-{rec1.pk}"')
        self.assertNotContains(response, 'Alex — Marketing')


class CRMDetailViewTest(CRMViewsBase):
    def test_detail_renders_all_sections(self):
        record = CRMRecord.objects.create(user=self.member)
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-detail-header"')
        self.assertContains(response, 'data-testid="crm-snapshot-card"')
        self.assertContains(response, 'data-testid="crm-plans-section"')
        self.assertContains(response, 'data-testid="crm-notes-section"')
        self.assertContains(response, 'data-testid="crm-experiments-section"')
        self.assertContains(response, 'data-testid="crm-content-context-section"')

    def test_detail_header_links_back_to_user_profile(self):
        record = CRMRecord.objects.create(user=self.member)
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertContains(response, 'data-testid="crm-detail-open-profile"')
        self.assertContains(
            response,
            f'href="/studio/users/{self.member.pk}/"',
        )

    def test_detail_shows_member_plans(self):
        record = CRMRecord.objects.create(user=self.member)
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertContains(response, 'Spring 2026')
        self.assertContains(
            response,
            f'href="/studio/plans/{plan.pk}/edit/"',
        )

    def test_detail_shows_member_notes_with_split(self):
        record = CRMRecord.objects.create(user=self.member)
        InterviewNote.objects.create(
            member=self.member, visibility='internal',
            body='Inner note body', created_by=self.staff,
        )
        InterviewNote.objects.create(
            member=self.member, visibility='external',
            body='Outer note body', created_by=self.staff,
        )
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertContains(response, 'data-testid="internal-notes"')
        self.assertContains(response, 'data-testid="external-notes"')
        self.assertContains(response, 'Inner note body')
        self.assertContains(response, 'Outer note body')

    def test_detail_shows_archive_button_when_active(self):
        record = CRMRecord.objects.create(user=self.member, status='active')
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertContains(response, 'data-testid="crm-detail-archive"')
        self.assertNotContains(response, 'data-testid="crm-detail-reactivate"')

    def test_detail_shows_reactivate_button_when_archived(self):
        record = CRMRecord.objects.create(user=self.member, status='archived')
        response = self.client.get(f'/studio/crm/{record.pk}/')
        self.assertContains(response, 'data-testid="crm-detail-reactivate"')
        self.assertNotContains(response, 'data-testid="crm-detail-archive"')


class CRMEditViewTest(CRMViewsBase):
    def test_edit_updates_snapshot_fields(self):
        record = CRMRecord.objects.create(user=self.member)
        response = self.client.post(
            f'/studio/crm/{record.pk}/edit',
            {
                'persona': 'Sam — Technical Pro',
                'summary': 'Backend engineer pivoting to LLM tooling',
                'next_steps': 'Pair on agents lesson',
            },
        )
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )
        record.refresh_from_db()
        self.assertEqual(record.persona, 'Sam — Technical Pro')
        self.assertEqual(
            record.summary,
            'Backend engineer pivoting to LLM tooling',
        )
        self.assertEqual(record.next_steps, 'Pair on agents lesson')

    def test_edit_is_post_only(self):
        record = CRMRecord.objects.create(user=self.member)
        response = self.client.get(f'/studio/crm/{record.pk}/edit')
        self.assertEqual(response.status_code, 405)

    def test_edit_truncates_persona_to_120_chars(self):
        record = CRMRecord.objects.create(user=self.member)
        self.client.post(
            f'/studio/crm/{record.pk}/edit',
            {'persona': 'x' * 200, 'summary': '', 'next_steps': ''},
        )
        record.refresh_from_db()
        self.assertEqual(len(record.persona), 120)


class CRMArchiveReactivateTest(CRMViewsBase):
    def test_archive_marks_archived(self):
        record = CRMRecord.objects.create(user=self.member, status='active')
        response = self.client.post(f'/studio/crm/{record.pk}/archive')
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )
        record.refresh_from_db()
        self.assertEqual(record.status, 'archived')

    def test_reactivate_marks_active(self):
        record = CRMRecord.objects.create(user=self.member, status='archived')
        response = self.client.post(f'/studio/crm/{record.pk}/reactivate')
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )
        record.refresh_from_db()
        self.assertEqual(record.status, 'active')

    def test_archive_post_only(self):
        record = CRMRecord.objects.create(user=self.member)
        response = self.client.get(f'/studio/crm/{record.pk}/archive')
        self.assertEqual(response.status_code, 405)

    def test_archived_record_drops_from_active_filter(self):
        record = CRMRecord.objects.create(user=self.member, status='active')
        self.client.post(f'/studio/crm/{record.pk}/archive')
        response = self.client.get('/studio/crm/')
        self.assertNotContains(response, f'data-testid="crm-row-{record.pk}"')


class CRMExperimentTest(CRMViewsBase):
    def test_create_experiment(self):
        record = CRMRecord.objects.create(user=self.member)
        response = self.client.post(
            f'/studio/crm/{record.pk}/experiments/new',
            {
                'title': 'Pair-program 1h/week',
                'hypothesis': 'Will increase shipping cadence',
                'status': 'running',
            },
        )
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )
        experiments = list(record.experiments.all())
        self.assertEqual(len(experiments), 1)
        self.assertEqual(experiments[0].title, 'Pair-program 1h/week')
        self.assertEqual(experiments[0].status, 'running')

    def test_create_experiment_requires_title(self):
        record = CRMRecord.objects.create(user=self.member)
        before = CRMExperiment.objects.count()
        self.client.post(
            f'/studio/crm/{record.pk}/experiments/new',
            {'title': '   ', 'hypothesis': 'x'},
        )
        self.assertEqual(CRMExperiment.objects.count(), before)

    def test_edit_experiment_updates_fields(self):
        record = CRMRecord.objects.create(user=self.member)
        exp = CRMExperiment.objects.create(
            crm_record=record,
            title='Initial',
            status='running',
        )
        response = self.client.post(
            f'/studio/crm/{record.pk}/experiments/{exp.pk}/edit',
            {
                'title': 'Updated title',
                'hypothesis': 'New hypothesis',
                'result': 'Cadence increased',
                'status': 'completed',
            },
        )
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )
        exp.refresh_from_db()
        self.assertEqual(exp.title, 'Updated title')
        self.assertEqual(exp.result, 'Cadence increased')
        self.assertEqual(exp.status, 'completed')

    def test_edit_experiment_get_renders_form(self):
        record = CRMRecord.objects.create(user=self.member)
        exp = CRMExperiment.objects.create(
            crm_record=record, title='Initial', status='running',
        )
        response = self.client.get(
            f'/studio/crm/{record.pk}/experiments/{exp.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-experiment-edit-title"')
        self.assertContains(response, 'value="Initial"')

    def test_delete_experiment_removes_row(self):
        record = CRMRecord.objects.create(user=self.member)
        exp = CRMExperiment.objects.create(
            crm_record=record, title='To delete',
        )
        response = self.client.post(
            f'/studio/crm/{record.pk}/experiments/{exp.pk}/delete',
        )
        self.assertRedirects(
            response, f'/studio/crm/{record.pk}/',
            fetch_redirect_response=False,
        )
        self.assertFalse(
            CRMExperiment.objects.filter(pk=exp.pk).exists(),
        )

    def test_delete_post_only(self):
        record = CRMRecord.objects.create(user=self.member)
        exp = CRMExperiment.objects.create(crm_record=record, title='X')
        response = self.client.get(
            f'/studio/crm/{record.pk}/experiments/{exp.pk}/delete',
        )
        self.assertEqual(response.status_code, 405)

    def test_experiment_404_when_crm_id_mismatches(self):
        record = CRMRecord.objects.create(user=self.member)
        other_record = CRMRecord.objects.create(user=self.other)
        exp = CRMExperiment.objects.create(crm_record=record, title='X')
        response = self.client.get(
            f'/studio/crm/{other_record.pk}/experiments/{exp.pk}/edit',
        )
        self.assertEqual(response.status_code, 404)


class CRMAccessControlTest(TestCase):
    """Anonymous redirects to login; non-staff returns 403."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(user=cls.member)
        cls.experiment = CRMExperiment.objects.create(
            crm_record=cls.record, title='Exp',
        )

    def test_list_redirects_anonymous_to_login(self):
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_detail_redirects_anonymous_to_login(self):
        response = self.client.get(f'/studio/crm/{self.record.pk}/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_list_returns_403_for_non_staff(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 403)

    def test_detail_returns_403_for_non_staff(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(f'/studio/crm/{self.record.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_edit_returns_403_for_non_staff(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(
            f'/studio/crm/{self.record.pk}/edit',
            {'persona': '', 'summary': '', 'next_steps': ''},
        )
        self.assertEqual(response.status_code, 403)

    def test_archive_returns_403_for_non_staff(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(
            f'/studio/crm/{self.record.pk}/archive',
        )
        self.assertEqual(response.status_code, 403)

    def test_experiment_create_returns_403_for_non_staff(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(
            f'/studio/crm/{self.record.pk}/experiments/new',
            {'title': 'x'},
        )
        self.assertEqual(response.status_code, 403)


class UserProfileCRMCTATest(TestCase):
    """The user profile's CRM card swaps state based on tracking."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.untracked = User.objects.create_user(
            email='untracked@test.com', password='pw',
        )
        cls.tracked = User.objects.create_user(
            email='tracked@test.com', password='pw',
        )
        cls.tracked_record = CRMRecord.objects.create(
            user=cls.tracked, created_by=cls.staff,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_untracked_user_shows_track_button(self):
        response = self.client.get(f'/studio/users/{self.untracked.pk}/')
        self.assertContains(response, 'data-testid="user-crm-section"')
        self.assertContains(response, 'data-testid="user-crm-cta-track"')
        self.assertContains(response, 'Not yet tracked in CRM')
        self.assertNotContains(response, 'data-testid="user-crm-cta-open"')
        # The form posts to the track endpoint.
        self.assertContains(
            response,
            f'action="/studio/users/{self.untracked.pk}/crm/track"',
        )

    def test_tracked_user_shows_open_link(self):
        response = self.client.get(f'/studio/users/{self.tracked.pk}/')
        self.assertContains(response, 'data-testid="user-crm-cta-open"')
        self.assertContains(response, 'Tracked since')
        self.assertContains(response, 'data-testid="user-crm-tracked-since"')
        self.assertNotContains(response, 'data-testid="user-crm-cta-track"')
        self.assertContains(
            response,
            f'href="/studio/crm/{self.tracked_record.pk}/"',
        )


class CRMSidebarNavTest(TestCase):
    """The studio sidebar has a CRM link under Members above Sprints."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sidebar_includes_crm_link(self):
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The sidebar link is rendered on every studio page; pick a
        # specific marker the CRM nav item carries (the visible label
        # plus the URL).
        self.assertIn('href="/studio/crm/"', body)
        self.assertIn('>CRM</span>', body)


class CRMStaffOnlyFieldsNotInMemberSurfacesTest(TestCase):
    """Staff CRM fields must never render on member-facing pages."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(
            user=cls.member,
            persona='Sam — The Technical Professional Moving to AI',
            summary='Backend engineer pivoting to LLM tooling',
            next_steps='Pair on agents lesson; review eval framework',
        )
        CRMExperiment.objects.create(
            crm_record=cls.record,
            title='Pair-program 1h/week',
            hypothesis='Will increase shipping cadence',
            result='cadence up 3x',
        )

    def test_account_page_does_not_leak_staff_crm_fields(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/account/')
        self.assertNotContains(response, 'Sam — The Technical Professional')
        self.assertNotContains(response, 'Backend engineer pivoting')
        self.assertNotContains(response, 'Pair on agents lesson')
        self.assertNotContains(response, 'Pair-program 1h/week')
        self.assertNotContains(response, 'cadence up 3x')
