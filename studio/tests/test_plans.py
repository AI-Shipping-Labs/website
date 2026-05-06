"""Studio admin views for plans (issue #432)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import InterviewNote, Plan, Sprint, Week

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
            member=cls.member_a, sprint=cls.sprint_x, status='draft',
        )
        cls.plan_a_y = Plan.objects.create(
            member=cls.member_a, sprint=cls.sprint_y, status='shared',
        )
        cls.plan_b_x = Plan.objects.create(
            member=cls.member_b, sprint=cls.sprint_x, status='active',
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

    def test_plan_list_filters_by_status(self):
        response = self.client.get('/studio/plans/?status=draft')
        ctx_plans = list(response.context['plans'])
        self.assertEqual(ctx_plans, [self.plan_a_x])

        # Only the draft plan's detail link is rendered as a row.
        self.assertContains(response, f'href="/studio/plans/{self.plan_a_x.pk}/"')
        self.assertNotContains(response, f'href="/studio/plans/{self.plan_a_y.pk}/"')
        self.assertNotContains(response, f'href="/studio/plans/{self.plan_b_x.pk}/"')

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
            'status': 'draft',
        })
        self.assertEqual(Plan.objects.count(), before + 1)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.status, 'draft')
        self.assertRedirects(response, f'/studio/plans/{plan.pk}/')

    def test_plan_create_rejects_duplicate_member_sprint(self):
        Plan.objects.create(
            member=self.member, sprint=self.sprint, status='draft',
        )
        before = Plan.objects.filter(
            member=self.member, sprint=self.sprint,
        ).count()
        response = self.client.post('/studio/plans/new', {
            'member': str(self.member.pk),
            'sprint': str(self.sprint.pk),
            'status': 'draft',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        self.assertEqual(
            Plan.objects.filter(
                member=self.member, sprint=self.sprint,
            ).count(),
            before,
        )


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
            summary_current_situation='SITN_VAL_X',
            summary_goal='GOAL_VAL_X',
            summary_main_gap='GAP_VAL_X',
            summary_weekly_hours='HOURS_VAL_X',
            summary_why_this_plan='WHY_VAL_X',
        )
        response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertEqual(response.status_code, 200)

        # Every summary field is rendered into a labelled <dd>. Asserting
        # on the data-field container ensures filtering or template
        # restructuring doesn't silently drop one of the bullets.
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

        # Regression: a multi-line {# ... #} above the internal block
        # used to leak as visible body text because Django's {# #}
        # comments do not span lines. Guard against any such leak.
        self.assertNotContains(response, '{# Internal')
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
            f'/studio/plans/{self.plan.pk}/notes/new',
        )
        self.assertEqual(response.status_code, 200)
        # Form context drives the selected option; assert on context to
        # avoid coupling to exact attribute ordering in the rendered HTML.
        self.assertEqual(response.context['form_data']['visibility'], 'internal')

        # POST without specifying ``visibility`` (e.g. JS-disabled
        # client) creates a row with internal visibility.
        response = self.client.post(
            f'/studio/plans/{self.plan.pk}/notes/new',
            {
                'kind': 'general',
                'body': 'note body',
            },
        )
        self.assertEqual(response.status_code, 302)
        note = InterviewNote.objects.filter(plan=self.plan).get()
        self.assertEqual(note.visibility, 'internal')

    def test_interview_note_create_external_creates_external_row(self):
        response = self.client.post(
            f'/studio/plans/{self.plan.pk}/notes/new',
            {
                'kind': 'general',
                'visibility': 'external',
                'body': 'shareable note',
            },
        )
        self.assertEqual(response.status_code, 302)
        note = InterviewNote.objects.filter(plan=self.plan).get()
        self.assertEqual(note.visibility, 'external')
        self.assertEqual(note.body, 'shareable note')
