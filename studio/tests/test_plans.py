"""Studio admin views for plans (issue #432)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CRMRecord
from plans.models import InterviewNote, NextStep, Plan, Sprint, Week
from questionnaires.models import (
    Answer,
    Questionnaire,
    Response,
    ResponseQuestion,
)

User = get_user_model()


class PlanAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def test_plan_list_requires_staff(self):
        # Anonymous: redirect to login.
        response = self.client.get('/studio/plans/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

        # Non-staff: 403.
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/plans/')
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # Staff: 200.
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/plans/')
        self.assertEqual(response.status_code, 200)

    def test_staff_can_reach_all_plan_pages(self):
        self.client.login(email='staff@test.com', password='pw')
        for url in [
            '/studio/plans/',
            '/studio/plans/new',
            f'/studio/plans/{self.plan.pk}/',
            f'/studio/plans/{self.plan.pk}/edit/',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, msg=f'{url} -> {response.status_code}')

    def test_non_staff_cannot_reach_plan_pages(self):
        self.client.login(email='member@test.com', password='pw')
        for url in [
            '/studio/plans/',
            '/studio/plans/new',
            f'/studio/plans/{self.plan.pk}/',
            f'/studio/plans/{self.plan.pk}/edit/',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, msg=f'{url} -> {response.status_code}')

    def test_detail_labels_prep_rows_as_pre_sprint_actions(self):
        NextStep.objects.create(
            plan=self.plan,
            description='Review workshop links',
            done_at=datetime.datetime(
                2026, 5, 1, 12, tzinfo=datetime.UTC,
            ),
        )
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/studio/plans/{self.plan.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pre-sprint actions')
        self.assertContains(response, 'Review workshop links')
        self.assertContains(response, '(done)')
        self.assertNotContains(response, 'Facilitator follow-up')


class PlanCreateMemberProfilePanelTest(TestCase):
    """The read-only "Member profile" side panel on plan-create (#883).

    The panel appears only when the form is pre-filled for a specific
    member via ``?user=<pk>``; it surfaces onboarding answers, CRM
    persona/summary/next-steps, and recent internal notes, and never
    leaks external notes or mutates anything.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='A',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='Spring', slug='spring-883',
            start_date=datetime.date(2026, 5, 1),
        )
        # Alice: onboarding + CRM + internal note.
        q = Questionnaire.objects.get(slug='onboarding-general')
        resp = Response.objects.create(
            questionnaire=q, respondent=cls.alice, status='submitted',
        )
        rq = ResponseQuestion.objects.create(
            response=resp, question_type='long_text',
            prompt='What are your goals?', order=0,
        )
        Answer.objects.create(
            response=resp, question=rq,
            text_value='Switch into AI engineering',
        )
        CRMRecord.objects.create(
            user=cls.alice, persona='Sam — Technical Professional',
            summary='Strong engineer', next_steps='Ship a RAG project',
        )
        InterviewNote.objects.create(
            member=cls.alice, visibility='internal',
            body='Motivated on the call',
        )
        InterviewNote.objects.create(
            member=cls.alice, visibility='external',
            body='Shareable external note',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_panel_shows_onboarding_persona_and_next_steps(self):
        response = self.client.get(f'/studio/plans/new?user={self.alice.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="member-profile-panel"')
        self.assertContains(response, 'What are your goals?')
        self.assertContains(response, 'Switch into AI engineering')
        self.assertContains(response, 'Sam — Technical Professional')
        self.assertContains(response, 'Ship a RAG project')
        self.assertContains(response, 'Motivated on the call')
        # The copy affordance is present when there is something to copy.
        self.assertContains(response, 'data-testid="member-profile-copy"')

    def test_panel_does_not_leak_external_notes(self):
        response = self.client.get(f'/studio/plans/new?user={self.alice.pk}')
        self.assertNotContains(response, 'Shareable external note')

    def test_no_panel_without_user_param(self):
        response = self.client.get('/studio/plans/new')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="member-profile-panel"')

    def test_panel_empty_states_for_member_without_onboarding_or_crm(self):
        response = self.client.get(f'/studio/plans/new?user={self.bob.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="member-profile-panel"')
        self.assertContains(
            response, 'data-testid="member-profile-onboarding-empty"',
        )
        self.assertContains(
            response, 'data-testid="member-profile-crm-empty"',
        )
        self.assertContains(
            response, 'data-testid="member-profile-notes-empty"',
        )

    def test_create_flow_still_works_with_panel(self):
        response = self.client.post('/studio/plans/new', {
            'member': str(self.alice.pk),
            'sprint': str(self.sprint.pk),
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Plan.objects.filter(member=self.alice, sprint=self.sprint).exists(),
        )


class PlanListFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member_a = User.objects.create_user(
            email='member-a@test.com', password='pw',
        )
        cls.member_b = User.objects.create_user(
            email='member-b@test.com', password='pw',
        )

        cls.sprint_x = Sprint.objects.create(
            name='Sprint X', slug='sprint-x',
            start_date=datetime.date(2026, 4, 1),
        )
        cls.sprint_y = Sprint.objects.create(
            name='Sprint Y', slug='sprint-y',
            start_date=datetime.date(2026, 6, 1),
        )

        cls.plan_a_x = Plan.objects.create(
            member=cls.member_a, sprint=cls.sprint_x,
            goal='goal A in X',
        )
        cls.plan_a_y = Plan.objects.create(
            member=cls.member_a, sprint=cls.sprint_y,
            goal='goal A in Y',
        )
        cls.plan_b_x = Plan.objects.create(
            member=cls.member_b, sprint=cls.sprint_x,
            goal='goal B in X',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_plan_list_filters_by_sprint(self):
        """Filtering by sprint shows only that sprint's plans.

        Asserts on the ``plans`` context queryset (the source of truth)
        AND on the rendered detail-link cells (so the table actually
        reflects the filter). The filter dropdown re-lists every
        sprint by name, so we cannot assert that "Sprint Y" is absent
        from the page entirely -- assert on the per-row member-link
        URLs which uniquely identify each plan row.
        """
        response = self.client.get(f'/studio/plans/?sprint={self.sprint_x.pk}')
        self.assertEqual(response.status_code, 200)

        ctx_plans = list(response.context['plans'])
        self.assertIn(self.plan_a_x, ctx_plans)
        self.assertIn(self.plan_b_x, ctx_plans)
        self.assertNotIn(self.plan_a_y, ctx_plans)

        # The two in-sprint rows render their detail link.
        self.assertContains(response, f'href="/studio/plans/{self.plan_a_x.pk}/"')
        self.assertContains(response, f'href="/studio/plans/{self.plan_b_x.pk}/"')
        # The out-of-sprint plan's row link is absent.
        self.assertNotContains(response, f'href="/studio/plans/{self.plan_a_y.pk}/"')

    def test_plan_list_has_no_status_filter_or_column(self):
        """Issue #728: the Plan.status field and its surface are gone.

        Status filter dropdown removed from the filter row; Status column
        removed from the table; table now has four columns
        (Member, Sprint, Shared, Actions).
        """
        response = self.client.get('/studio/plans/')
        self.assertEqual(response.status_code, 200)
        # No status filter input present in the filter form.
        self.assertNotContains(response, 'name="status"')
        # No Status column header in the table.
        self.assertNotContains(response, '>Status</th>')
        # The Shared column header is still there.
        self.assertContains(response, '>Shared</th>')

    def test_plan_list_filters_by_member(self):
        response = self.client.get(f'/studio/plans/?member={self.member_a.pk}')
        ctx_plans = list(response.context['plans'])
        self.assertEqual(set(ctx_plans), {self.plan_a_x, self.plan_a_y})
        self.assertNotIn(self.plan_b_x, ctx_plans)

        # member_a's two plan rows are present; member_b's row is not.
        self.assertContains(response, f'href="/studio/plans/{self.plan_a_x.pk}/"')
        self.assertContains(response, f'href="/studio/plans/{self.plan_a_y.pk}/"')
        self.assertNotContains(response, f'href="/studio/plans/{self.plan_b_x.pk}/"')


class PlanCreateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_plan_create_post_creates_plan_and_redirects(self):
        before = Plan.objects.count()
        response = self.client.post('/studio/plans/new', {
            'member': str(self.member.pk),
            'sprint': str(self.sprint.pk),
        })
        self.assertEqual(Plan.objects.count(), before + 1)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertRedirects(response, f'/studio/plans/{plan.pk}/')

    def test_plan_create_form_has_no_status_select(self):
        """Issue #728: the New plan form must not render a status select."""
        response = self.client.get('/studio/plans/new')
        self.assertEqual(response.status_code, 200)
        # No status <select> in the form (the only other status= reference
        # left in the codebase is sprint.status which lives on a different
        # form). Asserting the select is absent rather than just the
        # label so a future label-only render also fails the test.
        self.assertNotContains(response, 'name="status"')

    def test_plan_create_rejects_duplicate_member_sprint(self):
        Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )
        before = Plan.objects.filter(
            member=self.member, sprint=self.sprint,
        ).count()
        response = self.client.post('/studio/plans/new', {
            'member': str(self.member.pk),
            'sprint': str(self.sprint.pk),
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        self.assertEqual(
            Plan.objects.filter(
                member=self.member, sprint=self.sprint,
            ).count(),
            before,
        )

    def test_plan_create_get_prefills_from_query_params(self):
        """GET ?user=<pk>&sprint=<pk> pre-selects both fields (issues #719 + #735).

        The plan_request bell notification lands here; the member must
        be pre-populated in the picker AND the sprint must be selected
        in the sprint ``<select>`` so the operator one-clicks Create
        plan. After #735, the member field is a people-picker (not a
        ``<select>``): the visible search input must show the user's
        display name (or email) and the hidden ``name="member"`` input
        must hold the pk.
        """
        response = self.client.get(
            f'/studio/plans/new?user={self.member.pk}&sprint={self.sprint.pk}',
        )
        self.assertEqual(response.status_code, 200)
        # form_data carries the string-form ids so the inline prefill
        # script in form.html seeds the visible + hidden inputs.
        self.assertEqual(
            response.context['form_data']['member'], str(self.member.pk),
        )
        self.assertEqual(
            response.context['form_data']['sprint'], str(self.sprint.pk),
        )
        # The picker context: display name resolves to the email (no
        # first/last name set on the test user). The seed-script branch
        # only renders when prefill_member_display is non-empty.
        self.assertEqual(
            response.context['prefill_member_display'], self.member.email,
        )
        # The picker include renders its search input + hidden input
        # with the ``plan-member`` id_prefix.
        self.assertContains(response, 'id="plan-member-search"')
        self.assertContains(
            response,
            '<input type="hidden" name="member" id="plan-member-id">',
            html=False,
        )
        # The seed script assigns the display name into the visible
        # input and the pk into the hidden input.
        body = response.content.decode()
        self.assertIn(
            f"visible.value = '{self.member.email}'",
            body,
        )
        self.assertIn(
            f"hidden.value = '{self.member.pk}'",
            body,
        )
        # The sprint <select> still uses the legacy ``selected`` attr.
        self.assertContains(
            response,
            f'<option value="{self.sprint.pk}" selected>{self.sprint.name}</option>',
            html=False,
        )

    def test_plan_create_get_with_stale_ids_renders_empty_form(self):
        """Stale ?user / ?sprint silently fall through to empty form (#719).

        Operators may follow a bell notification long after the
        member or sprint has been deleted. No 400, no 404, no
        traceback -- just an empty form they can fill in by hand.
        """
        response = self.client.get(
            '/studio/plans/new?user=999999&sprint=888888',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form_data']['member'], '')
        self.assertEqual(response.context['form_data']['sprint'], '')

    def test_plan_create_get_with_non_digit_query_params_renders_empty_form(self):
        """Non-digit ?user / ?sprint fall through to empty form (#719)."""
        response = self.client.get(
            '/studio/plans/new?user=abc&sprint=xyz',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form_data']['member'], '')
        self.assertEqual(response.context['form_data']['sprint'], '')

    def test_plan_create_get_with_only_user_prefills_member_only(self):
        """Partial pre-fill works: only ``?user`` fills the member picker."""
        response = self.client.get(
            f'/studio/plans/new?user={self.member.pk}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['form_data']['member'], str(self.member.pk),
        )
        self.assertEqual(response.context['form_data']['sprint'], '')
        # No sprint => picker_extra_query is empty (no sprint-context
        # badges to compute).
        self.assertEqual(response.context['picker_extra_query'], '')

    def test_plan_create_get_renders_picker_not_select(self):
        """GET /studio/plans/new renders the picker (issue #735).

        The legacy ``<select name="member">`` is gone; the member field
        is now the include from ``_people_picker.html`` whose hidden
        ``<input name="member">`` is the actual form field.
        """
        response = self.client.get('/studio/plans/new')
        self.assertEqual(response.status_code, 200)
        # Picker hidden id input carries the form field name.
        self.assertContains(
            response,
            '<input type="hidden" name="member" id="plan-member-id">',
            html=False,
        )
        # Picker visible search input is present with the expected testid.
        self.assertContains(response, 'data-testid="plan-member-search"')
        # Legacy <select name="member"> is gone.
        self.assertNotContains(response, '<select name="member"')

    def test_plan_create_get_picker_extra_query_with_sprint(self):
        """``?sprint=<pk>`` plumbs into the picker's data-extra-query.

        The picker include reads ``data-extra-query`` and appends it
        to every search request, which is what lights up the sprint-
        context badges (``In this sprint``, ``Has plan in sprint``).
        """
        response = self.client.get(
            f'/studio/plans/new?sprint={self.sprint.pk}',
        )
        self.assertEqual(response.status_code, 200)
        # The sprint's slug is ``s`` in this test class; assert on the
        # exact URL-encoded value the view computes.
        self.assertEqual(
            response.context['picker_extra_query'], f'sprint={self.sprint.slug}',
        )
        self.assertContains(
            response,
            f'data-extra-query="sprint={self.sprint.slug}"',
        )

    def test_plan_create_get_stale_user_pk_does_not_seed_picker(self):
        """Stale ?user falls through silently to an empty picker (#719/#735).

        No traceback, no error banner, no leaked display name.
        """
        response = self.client.get('/studio/plans/new?user=999999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form_data']['member'], '')
        self.assertEqual(response.context['prefill_member_display'], '')
        # No seed script branch -> no ``visible.value`` assignment.
        self.assertNotContains(response, 'visible.value =')

    def test_plan_create_post_still_works_with_picker(self):
        """POST flow is unchanged by the picker swap (issue #735).

        The picker include's hidden ``<input name="member">`` carries
        the pk under the same key the legacy ``<select>`` did, so the
        existing validation in ``plan_create`` does not need any change.
        """
        before = Plan.objects.count()
        response = self.client.post('/studio/plans/new', {
            'member': str(self.member.pk),
            'sprint': str(self.sprint.pk),
        })
        self.assertEqual(Plan.objects.count(), before + 1)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertRedirects(response, f'/studio/plans/{plan.pk}/')


class PlanDetailRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_plan_detail_renders_summary_fields(self):
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            goal='SHORT_GOAL_VAL_X',
            summary_current_situation='SITN_VAL_X',
            summary_goal='GOAL_VAL_X',
            summary_main_gap='GAP_VAL_X',
            summary_weekly_hours='HOURS_VAL_X',
            summary_why_this_plan='WHY_VAL_X',
        )
        response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertEqual(response.status_code, 200)
        # Issue #702: hand-built link replaced by the shared
        # ``Open in Django admin`` partial; the chip now carries
        # ``data-testid="studio-open-in-admin"``.
        self.assertContains(response, 'data-testid="studio-open-in-admin"')
        self.assertContains(response, f'/admin/plans/plan/{plan.pk}/change/')

        # Every summary field is rendered into a labelled <dd>. Asserting
        # on the data-field container ensures filtering or template
        # restructuring doesn't silently drop one of the bullets.
        self.assertContains(response, 'data-field="goal"')
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1 whitespace-pre-line">SHORT_GOAL_VAL_X</dd>',
            html=True,
        )
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1 whitespace-pre-line">SITN_VAL_X</dd>',
            html=True,
        )
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1 whitespace-pre-line">GOAL_VAL_X</dd>',
            html=True,
        )
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1 whitespace-pre-line">GAP_VAL_X</dd>',
            html=True,
        )
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1">HOURS_VAL_X</dd>',
            html=True,
        )
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1 whitespace-pre-line">WHY_VAL_X</dd>',
            html=True,
        )

    def test_plan_detail_renders_empty_goal_dash(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        response = self.client.get(f'/studio/plans/{plan.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-field="goal"')
        self.assertContains(
            response,
            '<dd class="text-foreground mt-1 whitespace-pre-line">-</dd>',
            html=True,
        )

    def test_plan_detail_separates_internal_and_external_notes(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)
        InterviewNote.objects.create(
            plan=plan, member=self.member,
            visibility='internal', body='INTERNAL_BODY',
        )
        InterviewNote.objects.create(
            plan=plan, member=self.member,
            visibility='external', body='EXTERNAL_BODY',
        )

        response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertEqual(response.status_code, 200)

        # Internal block heading must include "staff only".
        self.assertContains(
            response,
            '<h3 data-testid="internal-notes-heading" class="text-md font-semibold text-foreground mb-3">Internal notes (staff only)</h3>',
            html=True,
        )
        self.assertContains(
            response,
            '<h3 data-testid="external-notes-heading" class="text-md font-semibold text-foreground mb-3">External notes (shareable with member)</h3>',
            html=True,
        )

        # Both bodies render somewhere on the page; their containers
        # have distinct testids so they cannot collide.
        self.assertContains(response, 'INTERNAL_BODY')
        self.assertContains(response, 'EXTERNAL_BODY')

        # Regression: multi-line {# ... #} comments render their middle
        # lines as body text. Guard against Django comment markers and
        # the previously leaked prose on the plan detail page.
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '#}')
        self.assertNotContains(response, 'Internal block:')
        self.assertNotContains(response, 'staff only" so a glance')

        # The view passes scoped querysets to the template; verify they
        # contain the right note (catches a bug where both blocks were
        # accidentally fed the same queryset).
        internal_qs = list(response.context['internal_notes'])
        external_qs = list(response.context['external_notes'])
        self.assertEqual(len(internal_qs), 1)
        self.assertEqual(internal_qs[0].body, 'INTERNAL_BODY')
        self.assertEqual(len(external_qs), 1)
        self.assertEqual(external_qs[0].body, 'EXTERNAL_BODY')

    def test_plan_detail_renders_for_4_week_sprint(self):
        sprint4 = Sprint.objects.create(
            name='Short', slug='short',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )
        plan = Plan.objects.create(member=self.member, sprint=sprint4)
        for n in range(1, 5):
            Week.objects.create(plan=plan, week_number=n, position=n)

        response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertEqual(response.status_code, 200)

        rendered_weeks = response.context['weeks']
        self.assertEqual(
            list(rendered_weeks.values_list('week_number', flat=True)),
            [1, 2, 3, 4],
        )
        # No phantom Week 5 / 6 placeholders.
        self.assertNotContains(response, 'data-week-number="5"')
        self.assertNotContains(response, 'data-week-number="6"')

    def test_plan_detail_renders_for_8_week_sprint(self):
        sprint8 = Sprint.objects.create(
            name='Long', slug='long',
            start_date=datetime.date(2026, 5, 1), duration_weeks=8,
        )
        plan = Plan.objects.create(member=self.member, sprint=sprint8)
        for n in range(1, 9):
            Week.objects.create(plan=plan, week_number=n, position=n)

        response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertEqual(response.status_code, 200)

        rendered_weeks = response.context['weeks']
        self.assertEqual(
            list(rendered_weeks.values_list('week_number', flat=True)),
            [1, 2, 3, 4, 5, 6, 7, 8],
        )
        # No phantom Week 9 placeholder.
        self.assertNotContains(response, 'data-week-number="9"')

    def test_plan_detail_renders_view_as_member_post_form(self):
        plan = Plan.objects.create(member=self.member, sprint=self.sprint)

        response = self.client.get(f'/studio/plans/{plan.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="studio-plan-view-as-member"')
        self.assertContains(
            response,
            f'action="/studio/plans/{plan.pk}/view-as-member/"',
        )
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')


class PlanViewAsMemberTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-view-as@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-view-as@test.com', password='pw',
        )
        cls.other_member = User.objects.create_user(
            email='other-view-as@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='View Sprint',
            slug='view-sprint',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def _url(self, plan=None):
        target = plan or self.plan
        return f'/studio/plans/{target.pk}/view-as-member/'

    def test_post_logs_in_as_plan_owner_and_redirects_to_owner_plan(self):
        self.client.login(email='staff-view-as@test.com', password='pw')

        response = self.client.post(self._url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/sprints/{self.sprint.slug}/plan/{self.plan.pk}',
        )
        self.assertEqual(self.client.session['_impersonator_id'], self.staff.pk)
        workspace = self.client.get(response['Location'])
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.wsgi_request.user, self.member)
        self.assertContains(workspace, 'You are logged in as member-view-as@test.com')
        self.assertContains(workspace, 'Return to your account')

    def test_get_returns_405_and_keeps_staff_session(self):
        self.client.login(email='staff-view-as@test.com', password='pw')

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 405)
        self.assertNotIn('_impersonator_id', self.client.session)
        home = self.client.get('/studio/plans/')
        self.assertEqual(home.wsgi_request.user, self.staff)

    def test_anonymous_redirects_to_login(self):
        response = self.client.post(self._url())

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertNotIn('_impersonator_id', self.client.session)

    def test_non_staff_forbidden_and_not_impersonated(self):
        self.client.login(email='other-view-as@test.com', password='pw')

        response = self.client.post(self._url())

        self.assertEqual(response.status_code, 403)
        self.assertNotIn('_impersonator_id', self.client.session)
        home = self.client.get('/')
        self.assertEqual(home.wsgi_request.user, self.other_member)

    def test_unknown_plan_returns_404(self):
        self.client.login(email='staff-view-as@test.com', password='pw')

        response = self.client.post('/studio/plans/999999/view-as-member/')

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('_impersonator_id', self.client.session)


class InterviewNoteCreateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_interview_note_create_defaults_to_internal(self):
        # GET: visibility selector pre-selects ``internal``.
        response = self.client.get(
            f'/studio/users/{self.member.pk}/notes/new',
        )
        self.assertEqual(response.status_code, 200)
        # Form context drives the selected option; assert on context to
        # avoid coupling to exact attribute ordering in the rendered HTML.
        self.assertEqual(response.context['form_data']['visibility'], 'internal')

        # POST without specifying ``visibility`` (e.g. JS-disabled
        # client) creates a row with internal visibility.
        response = self.client.post(
            f'/studio/users/{self.member.pk}/notes/new',
            {
                'kind': 'intake',
                'body': 'note body',
            },
        )
        self.assertEqual(response.status_code, 302)
        note = InterviewNote.objects.filter(member=self.member).get()
        self.assertEqual(note.visibility, 'internal')
        self.assertIsNone(note.plan)

    def test_interview_note_create_external_creates_external_row(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/notes/new',
            {
                'kind': 'general',
                'visibility': 'external',
                'plan_id': str(self.plan.pk),
                'body': 'shareable note',
            },
        )
        self.assertEqual(response.status_code, 302)
        note = InterviewNote.objects.filter(plan=self.plan).get()
        self.assertEqual(note.visibility, 'external')
        self.assertEqual(note.body, 'shareable note')
