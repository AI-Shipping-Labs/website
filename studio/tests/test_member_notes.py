"""Studio member-note UX regressions for issue #459."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class MemberNotesTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com',
            password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com',
            password='pw',
        )
        cls.spring = Sprint.objects.create(
            name='Spring 2026',
            slug='spring-2026',
            start_date=datetime.date(2026, 3, 1),
        )
        cls.summer = Sprint.objects.create(
            name='Summer 2026',
            slug='summer-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.spring_plan = Plan.objects.create(
            member=cls.member,
            sprint=cls.spring,
        )
        cls.summer_plan = Plan.objects.create(
            member=cls.member,
            sprint=cls.summer,
        )
        cls.other_plan = Plan.objects.create(
            member=cls.other,
            sprint=cls.spring,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')


class UserDetailMemberNotesTest(MemberNotesTestBase):
    """After issue #560 the user profile no longer renders member notes inline.

    Notes live on the CRM record now. These tests pin the absence so a
    regression that reintroduces the section gets caught.
    """

    def test_user_detail_does_not_render_member_notes_section(self):
        # Even with notes attached, the user profile must not surface
        # them — they belong on the CRM record now.
        InterviewNote.objects.create(
            plan=self.spring_plan,
            member=self.member,
            visibility='internal',
            kind='meeting',
            body='Discussed pivot from RAG to agents',
            created_by=self.staff,
        )
        InterviewNote.objects.create(
            plan=None,
            member=self.member,
            visibility='external',
            kind='general',
            body='Share weekly progress',
            created_by=self.staff,
        )
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="member-notes-section"')
        # The body text of the notes must not leak inline either.
        self.assertNotContains(response, 'Discussed pivot from RAG to agents')
        self.assertNotContains(response, 'Share weekly progress')


class MemberNoteCreateEditDeleteTest(MemberNotesTestBase):
    def test_create_form_defaults_and_plan_prefill(self):
        response = self.client.get(
            f'/studio/users/{self.member.pk}/notes/new?plan_id={self.spring_plan.pk}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form_data']['visibility'], 'internal')
        self.assertEqual(response.context['form_data']['kind'], 'intake')
        self.assertEqual(
            response.context['form_data']['plan_id'],
            str(self.spring_plan.pk),
        )
        self.assertContains(response, 'Not tied to a sprint')
        self.assertContains(response, 'Spring 2026')
        self.assertContains(response, 'Summer 2026')

    def test_create_saves_member_note_and_redirects_to_anchor(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/notes/new',
            {
                'kind': 'meeting',
                'visibility': 'external',
                'plan_id': str(self.spring_plan.pk),
                'body': 'Meeting note body',
            },
            follow=True,
        )
        self.assertRedirects(
            response,
            f'/studio/users/{self.member.pk}/#member-notes',
        )
        note = InterviewNote.objects.get(body='Meeting note body')
        self.assertEqual(note.member, self.member)
        self.assertEqual(note.plan, self.spring_plan)
        self.assertEqual(note.created_by, self.staff)
        self.assertContains(response, 'Member note added.')

    def test_create_with_tampered_other_member_plan_stores_unattached(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/notes/new',
            {
                'kind': 'intake',
                'visibility': 'internal',
                'plan_id': str(self.other_plan.pk),
                'body': 'Tampered plan id note',
            },
        )
        self.assertEqual(response.status_code, 302)
        note = InterviewNote.objects.get(body='Tampered plan id note')
        self.assertEqual(note.member, self.member)
        self.assertIsNone(note.plan)

    def test_non_staff_create_returns_403(self):
        self.client.logout()
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(f'/studio/users/{self.member.pk}/notes/new')
        self.assertEqual(response.status_code, 403)

    def test_edit_404s_when_url_user_does_not_match_note_member(self):
        note = InterviewNote.objects.create(
            plan=None,
            member=self.member,
            visibility='internal',
            body='Private note',
            created_by=self.staff,
        )
        response = self.client.get(
            f'/studio/users/{self.other.pk}/notes/{note.pk}/edit',
        )
        self.assertEqual(response.status_code, 404)

    def test_edit_can_move_note_to_unattached(self):
        note = InterviewNote.objects.create(
            plan=self.spring_plan,
            member=self.member,
            visibility='internal',
            kind='meeting',
            body='Before',
            created_by=self.staff,
        )
        response = self.client.post(
            f'/studio/users/{self.member.pk}/notes/{note.pk}/edit',
            {
                'kind': 'general',
                'visibility': 'external',
                'plan_id': '',
                'body': 'After',
            },
        )
        self.assertRedirects(
            response,
            f'/studio/users/{self.member.pk}/#member-notes',
        )
        note.refresh_from_db()
        self.assertIsNone(note.plan)
        self.assertEqual(note.kind, 'general')
        self.assertEqual(note.visibility, 'external')
        self.assertEqual(note.body, 'After')

    def test_delete_removes_note_and_redirects_to_anchor(self):
        note = InterviewNote.objects.create(
            plan=None,
            member=self.member,
            visibility='internal',
            body='Delete me',
            created_by=self.staff,
        )
        response = self.client.post(
            f'/studio/users/{self.member.pk}/notes/{note.pk}/delete',
            follow=True,
        )
        self.assertRedirects(
            response,
            f'/studio/users/{self.member.pk}/#member-notes',
        )
        self.assertFalse(InterviewNote.objects.filter(pk=note.pk).exists())
        self.assertContains(response, 'Member note deleted.')


class PlanDetailMemberNotesTest(MemberNotesTestBase):
    def test_plan_detail_lists_all_member_notes_with_sprint_badges(self):
        spring_note = InterviewNote.objects.create(
            plan=self.spring_plan,
            member=self.member,
            visibility='internal',
            kind='meeting',
            body='Spring meeting note',
            created_by=self.staff,
        )
        summer_note = InterviewNote.objects.create(
            plan=self.summer_plan,
            member=self.member,
            visibility='internal',
            kind='intake',
            body='Summer meeting note',
            created_by=self.staff,
        )
        InterviewNote.objects.create(
            plan=None,
            member=self.member,
            visibility='external',
            kind='general',
            body='Global member note',
            created_by=self.staff,
        )
        InterviewNote.objects.create(
            plan=self.other_plan,
            member=self.other,
            visibility='internal',
            body='Other member note',
            created_by=self.staff,
        )

        response = self.client.get(f'/studio/plans/{self.summer_plan.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Member notes')
        self.assertNotContains(response, 'Interview notes')
        self.assertContains(response, 'Spring meeting note')
        self.assertContains(response, 'Summer meeting note')
        self.assertContains(response, 'Global member note')
        self.assertNotContains(response, 'Other member note')
        self.assertContains(response, 'This sprint')
        self.assertContains(
            response,
            f'href="/studio/plans/{self.spring_plan.pk}/"',
        )
        self.assertContains(response, 'Spring 2026')
        self.assertContains(
            response,
            f'href="/studio/users/{self.member.pk}/notes/{spring_note.pk}/edit"',
        )
        self.assertContains(
            response,
            f'action="/studio/users/{self.member.pk}/notes/{summer_note.pk}/delete"',
        )
        self.assertContains(
            response,
            (
                f'href="/studio/users/{self.member.pk}/notes/new'
                f'?plan_id={self.summer_plan.pk}"'
            ),
        )

        internal_qs = list(response.context['internal_notes'])
        self.assertEqual(
            {note.pk for note in internal_qs},
            {spring_note.pk, summer_note.pk},
        )


class LegacyPlanNoteRedirectTest(MemberNotesTestBase):
    def test_legacy_create_redirects_to_member_note_form(self):
        response = self.client.get(
            f'/studio/plans/{self.spring_plan.pk}/notes/new',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            (
                f'/studio/users/{self.member.pk}/notes/new'
                f'?plan_id={self.spring_plan.pk}'
            ),
        )

    def test_legacy_edit_redirects_to_member_note_edit(self):
        note = InterviewNote.objects.create(
            plan=self.spring_plan,
            member=self.member,
            visibility='internal',
            body='Legacy edit',
            created_by=self.staff,
        )
        response = self.client.get(
            f'/studio/plans/{self.spring_plan.pk}/notes/{note.pk}/edit',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'/studio/users/{self.member.pk}/notes/{note.pk}/edit',
        )

    def test_legacy_delete_post_redirects_307_to_member_note_delete(self):
        note = InterviewNote.objects.create(
            plan=self.spring_plan,
            member=self.member,
            visibility='internal',
            body='Legacy delete',
            created_by=self.staff,
        )
        response = self.client.post(
            f'/studio/plans/{self.spring_plan.pk}/notes/{note.pk}/delete',
        )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response['Location'],
            f'/studio/users/{self.member.pk}/notes/{note.pk}/delete',
        )

        follow_response = self.client.post(response['Location'])
        self.assertRedirects(
            follow_response,
            f'/studio/users/{self.member.pk}/#member-notes',
        )
        self.assertFalse(InterviewNote.objects.filter(pk=note.pk).exists())
