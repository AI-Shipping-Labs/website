"""Tests for participant week-note CRUD endpoints (issue #499).

Covers the three URL routes added in ``plans/urls.py``:

- ``POST /account/plan/<plan_id>/weeks/<week_id>/notes``
- ``POST /account/plan/<plan_id>/week-notes/<note_id>`` (update)
- ``POST /account/plan/<plan_id>/week-notes/<note_id>/delete``

Plus the rendering invariants on the owner page (notes appear under
the right week, newest first; teammate cohort view shows notes
read-only; ``InterviewNote`` rows do NOT leak onto member-facing
pages).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import (
    InterviewNote,
    Plan,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)

User = get_user_model()


class WeekNoteCreateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Sprint Notes', slug='sprint-notes',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw', first_name='Olivia',
        )
        cls.intruder = User.objects.create_user(
            email='intruder@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='cohort',
        )
        cls.other_plan = Plan.objects.create(
            member=cls.intruder, sprint=cls.sprint, visibility='cohort',
        )
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        cls.foreign_week = Week.objects.create(
            plan=cls.other_plan, week_number=1,
        )

    def _create_url(self, plan, week):
        return reverse(
            'week_note_create',
            kwargs={'plan_id': plan.pk, 'week_id': week.pk},
        )

    def test_owner_can_create_note(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._create_url(self.plan, self.week),
            data={'body': 'Finished data import, blocked on evals'},
        )
        self.assertEqual(response.status_code, 302)
        notes = list(self.week.notes.all())
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].body, 'Finished data import, blocked on evals')
        self.assertEqual(notes[0].author_id, self.owner.id)

    def test_blank_body_returns_400(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._create_url(self.plan, self.week),
            data={'body': '   '},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.week.notes.count(), 0)

    def test_anonymous_cannot_create(self):
        response = self.client.post(
            self._create_url(self.plan, self.week),
            data={'body': 'sneaky'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertEqual(self.week.notes.count(), 0)

    def test_non_owner_cannot_create(self):
        self.client.force_login(self.intruder)
        response = self.client.post(
            self._create_url(self.plan, self.week),
            data={'body': 'not allowed'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.week.notes.count(), 0)

    def test_week_must_belong_to_plan(self):
        """Owner posting with a foreign week ID gets 404."""
        self.client.force_login(self.owner)
        response = self.client.post(
            self._create_url(self.plan, self.foreign_week),
            data={'body': 'mismatched week'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.foreign_week.notes.count(), 0)

    def test_get_method_not_allowed(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._create_url(self.plan, self.week))
        self.assertEqual(response.status_code, 405)


class WeekNoteUpdateDeleteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Sprint Notes 2', slug='sprint-notes-2',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner2@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other2@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='cohort',
        )
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        # Author is the owner; the second note is also authored by
        # owner in normal flow -- a stray "foreign-author" row is
        # used only to defend the author check.
        cls.owner_note = WeekNote.objects.create(
            week=cls.week, body='original body', author=cls.owner,
        )
        cls.foreign_authored = WeekNote.objects.create(
            week=cls.week, body='foreign author body', author=cls.other,
        )

    def _update_url(self, plan, note):
        return reverse(
            'week_note_update',
            kwargs={'plan_id': plan.pk, 'note_id': note.pk},
        )

    def _delete_url(self, plan, note):
        return reverse(
            'week_note_delete',
            kwargs={'plan_id': plan.pk, 'note_id': note.pk},
        )

    def test_owner_can_update_own_note(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._update_url(self.plan, self.owner_note),
            data={'body': 'edited body'},
        )
        self.assertEqual(response.status_code, 302)
        self.owner_note.refresh_from_db()
        self.assertEqual(self.owner_note.body, 'edited body')

    def test_blank_update_returns_400(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._update_url(self.plan, self.owner_note),
            data={'body': '   '},
        )
        self.assertEqual(response.status_code, 400)
        self.owner_note.refresh_from_db()
        self.assertEqual(self.owner_note.body, 'original body')

    def test_owner_cannot_edit_another_authors_note(self):
        """Even on their own plan the owner cannot rewrite another author's note."""
        self.client.force_login(self.owner)
        response = self.client.post(
            self._update_url(self.plan, self.foreign_authored),
            data={'body': 'try to overwrite'},
        )
        self.assertEqual(response.status_code, 404)
        self.foreign_authored.refresh_from_db()
        self.assertEqual(self.foreign_authored.body, 'foreign author body')

    def test_non_owner_cannot_edit(self):
        self.client.force_login(self.other)
        response = self.client.post(
            self._update_url(self.plan, self.owner_note),
            data={'body': 'overwrite'},
        )
        self.assertEqual(response.status_code, 404)
        self.owner_note.refresh_from_db()
        self.assertEqual(self.owner_note.body, 'original body')

    def test_owner_can_delete_own_note(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._delete_url(self.plan, self.owner_note),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(WeekNote.objects.filter(pk=self.owner_note.pk).exists())

    def test_owner_cannot_delete_another_authors_note(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self._delete_url(self.plan, self.foreign_authored),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            WeekNote.objects.filter(pk=self.foreign_authored.pk).exists(),
        )

    def test_anonymous_cannot_update(self):
        response = self.client.post(
            self._update_url(self.plan, self.owner_note),
            data={'body': 'sneaky'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.owner_note.refresh_from_db()
        self.assertEqual(self.owner_note.body, 'original body')


class MyPlanRendersWeekNotesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Render Sprint', slug='render-sprint',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner-render@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='cohort',
        )
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        cls.note_old = WeekNote.objects.create(
            week=cls.week, body='OLDEST_NOTE_MARKER', author=cls.owner,
        )
        cls.note_new = WeekNote.objects.create(
            week=cls.week, body='NEWEST_NOTE_MARKER', author=cls.owner,
        )

    def test_owner_page_renders_notes_with_edit_form(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('my_plan_detail', kwargs={'plan_id': self.plan.pk}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'OLDEST_NOTE_MARKER')
        self.assertContains(response, 'NEWEST_NOTE_MARKER')
        # Owner sees the add-note textarea
        self.assertContains(response, 'data-testid="plan-week-note-add-form"')
        self.assertContains(response, 'data-testid="plan-week-note-add-textarea"')
        # Owner sees the edit/delete controls on their own notes
        self.assertContains(response, 'data-testid="plan-week-note-edit"')
        self.assertContains(response, 'data-testid="plan-week-note-delete"')

    def test_owner_page_orders_notes_newest_first(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('my_plan_detail', kwargs={'plan_id': self.plan.pk}),
        )
        body = response.content.decode('utf-8')
        new_pos = body.find('NEWEST_NOTE_MARKER')
        old_pos = body.find('OLDEST_NOTE_MARKER')
        self.assertGreater(new_pos, 0)
        self.assertGreater(old_pos, 0)
        self.assertLess(new_pos, old_pos, 'newest note must render first')

    def test_owner_page_does_not_render_internal_interview_note(self):
        InterviewNote.objects.create(
            plan=self.plan, member=self.owner,
            visibility='internal', body='SECRET_INTERNAL_DETAIL',
        )
        InterviewNote.objects.create(
            plan=self.plan, member=self.owner,
            visibility='external', body='EXTERNAL_DETAIL_PRIVATE',
        )
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('my_plan_detail', kwargs={'plan_id': self.plan.pk}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'SECRET_INTERNAL_DETAIL')
        self.assertNotContains(response, 'EXTERNAL_DETAIL_PRIVATE')


class TeammatePlanRendersWeekNotesReadOnlyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Read-only Sprint', slug='read-only-sprint',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.alice = User.objects.create_user(
            email='alice-ro@test.com', password='pw',
        )
        cls.bob = User.objects.create_user(
            email='bob-ro@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, visibility='cohort',
        )
        # Plan creation auto-enrolls Alice via ``plans.signals``;
        # we only need an explicit row for the teammate.
        SprintEnrollment.objects.get_or_create(
            sprint=cls.sprint, user=cls.bob,
        )
        cls.week = Week.objects.create(plan=cls.plan, week_number=1)
        WeekNote.objects.create(
            week=cls.week,
            body='TEAMMATE_VISIBLE_NOTE',
            author=cls.alice,
        )

    def _url(self):
        return reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.plan.pk,
            },
        )

    def test_teammate_sees_owner_notes(self):
        self.client.force_login(self.bob)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'TEAMMATE_VISIBLE_NOTE')

    def test_teammate_does_not_see_edit_or_delete_controls(self):
        self.client.force_login(self.bob)
        response = self.client.get(self._url())
        self.assertNotContains(response, 'data-testid="plan-week-note-add-form"')
        self.assertNotContains(response, 'data-testid="plan-week-note-edit"')
        self.assertNotContains(response, 'data-testid="plan-week-note-delete"')

    def test_teammate_view_does_not_render_interview_note(self):
        InterviewNote.objects.create(
            plan=self.plan, member=self.alice,
            visibility='internal', body='SECRET_INTERNAL_VIA_TEAMMATE',
        )
        self.client.force_login(self.bob)
        response = self.client.get(self._url())
        self.assertNotContains(response, 'SECRET_INTERNAL_VIA_TEAMMATE')
