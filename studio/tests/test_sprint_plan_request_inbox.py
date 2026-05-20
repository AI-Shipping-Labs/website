"""Pending plan requests inbox on the sprint detail page (issue #718).

Covers the inbox panel rendering, the POST endpoint that creates a
plan from a pending ``PlanRequest``, the staff-only access matrix,
idempotency under double-click, audit-row preservation, the sprint
list "Pending requests" column, and the ``#pending-requests`` deep
link.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from plans.models import Plan, PlanRequest, Sprint, SprintEnrollment

User = get_user_model()


def _make_sprint(name='May 2026', slug='may-2026'):
    return Sprint.objects.create(
        name=name, slug=slug,
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=6,
    )


class PendingRequestsPanelRenderTest(TestCase):
    """The inbox panel filters by the (request, no plan) left-anti-join."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = _make_sprint()
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Brown',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            first_name='Bob', last_name='Smith',
        )
        cls.carol = User.objects.create_user(
            email='carol@test.com', password='pw',
            first_name='Carol', last_name='Davis',
        )
        # Alice: 2 requests, no plan -> appears.
        PlanRequest.objects.create(sprint=cls.sprint, member=cls.alice)
        PlanRequest.objects.create(sprint=cls.sprint, member=cls.alice)
        # Bob: 1 request, already has a plan -> excluded.
        PlanRequest.objects.create(sprint=cls.sprint, member=cls.bob)
        Plan.objects.create(sprint=cls.sprint, member=cls.bob)
        # Carol: 1 request, no plan -> appears.
        PlanRequest.objects.create(sprint=cls.sprint, member=cls.carol)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_panel_shows_only_members_with_request_and_no_plan(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertEqual(response.status_code, 200)

        rows = response.context['pending_plan_requests']
        member_ids = {row['member'].pk for row in rows}
        self.assertEqual(member_ids, {self.alice.pk, self.carol.pk})
        # Bob has a plan -> excluded by the left-anti-join.
        self.assertNotIn(self.bob.pk, member_ids)

    def test_panel_section_anchor_present(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(response, 'id="pending-requests"')

    def test_panel_heading_shows_count(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        # Alice + Carol = 2 distinct members in the inbox.
        self.assertContains(response, 'Pending plan requests (2)')

    def test_panel_row_shows_display_name_email_count_and_timestamp(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        # Alice has 2 requests; her full name renders.
        self.assertContains(response, 'Alice Brown')
        self.assertContains(response, 'alice@test.com')
        self.assertContains(response, 'Requested 2 times, last at')

    def test_panel_row_uses_no_name_fallback_when_user_has_no_full_name(self):
        nameless = User.objects.create_user(
            email='nameless@test.com', password='pw',
        )
        PlanRequest.objects.create(sprint=self.sprint, member=nameless)
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(response, '(no name)')

    def test_panel_row_has_csrf_protected_post_form_to_create_plan(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        body = response.content.decode()
        # The form points at the new endpoint with method=post.
        expected_action = (
            f'/studio/sprints/{self.sprint.pk}/plan-requests/'
            f'{self.alice.pk}/create-plan/'
        )
        self.assertIn(f'action="{expected_action}"', body)
        self.assertIn('method="post"', body)
        # CSRF input must be on the same form (the test client adds it
        # via {% csrf_token %} in the template).
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_member_with_request_and_plan_not_in_panel(self):
        """Defence in depth: filter by JOIN, not by template ``{% if %}``."""
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        rows = response.context['pending_plan_requests']
        self.assertFalse(
            any(row['member'].pk == self.bob.pk for row in rows),
            msg='Bob has a plan; the inbox must NOT include him.',
        )


class PendingRequestsEmptyStateTest(TestCase):
    """The panel renders cleanly when nothing is pending."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = _make_sprint(name='June 2026', slug='june-2026')

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_empty_state_copy_renders(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(
            response,
            'No pending plan requests for this sprint.',
        )
        self.assertContains(
            response, 'data-testid="sprint-pending-requests-empty"',
        )

    def test_empty_state_does_not_hide_secondary_add_member_path(self):
        """Empty inbox still renders the ``Add member`` details block."""
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(
            response, 'data-testid="sprint-add-member-link"',
        )
        self.assertContains(response, 'Add member without a request')

    def test_section_anchor_present_even_when_empty(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(response, 'id="pending-requests"')


class CreatePlanFromRequestAccessControlTest(TestCase):
    """Staff-only POST endpoint; anonymous redirects, non-staff 403s."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member_non_staff = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.requester = User.objects.create_user(
            email='requester@test.com', password='pw',
        )
        cls.sprint = _make_sprint()
        PlanRequest.objects.create(sprint=cls.sprint, member=cls.requester)

    def _url(self):
        return (
            f'/studio/sprints/{self.sprint.pk}/plan-requests/'
            f'{self.requester.pk}/create-plan/'
        )

    def test_anonymous_post_redirects_to_login_and_no_plan_created(self):
        before = Plan.objects.count()
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertEqual(Plan.objects.count(), before)

    def test_non_staff_post_returns_403_and_no_plan_created(self):
        self.client.login(email='member@test.com', password='pw')
        before = Plan.objects.count()
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Plan.objects.count(), before)

    def test_staff_get_returns_405_method_not_allowed(self):
        """The endpoint is POST-only; GET must not create a plan."""
        self.client.login(email='staff@test.com', password='pw')
        before = Plan.objects.count()
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Plan.objects.count(), before)

    def test_unknown_sprint_returns_404(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.post(
            f'/studio/sprints/9999999/plan-requests/'
            f'{self.requester.pk}/create-plan/'
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_member_returns_404(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/plan-requests/'
            f'9999999/create-plan/'
        )
        self.assertEqual(response.status_code, 404)


class CreatePlanFromRequestSubmitTest(TestCase):
    """Happy path: POST creates plan + enrollment, redirects, flashes."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Brown',
        )
        cls.sprint = _make_sprint()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        # One pending request as the inbox precondition.
        PlanRequest.objects.create(sprint=self.sprint, member=self.member)

    def _url(self):
        return (
            f'/studio/sprints/{self.sprint.pk}/plan-requests/'
            f'{self.member.pk}/create-plan/'
        )

    def test_post_creates_plan_and_enrollment_and_redirects_to_editor(self):
        response = self.client.post(self._url())
        plan = Plan.objects.get(sprint=self.sprint, member=self.member)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], f'/studio/plans/{plan.pk}/edit/',
        )
        enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint, user=self.member,
        )
        self.assertEqual(enrollment.enrolled_by_id, self.staff.pk)
        # Weeks materialised by the service helper.
        self.assertEqual(plan.weeks.count(), self.sprint.duration_weeks)

    def test_post_flashes_success_message(self):
        response = self.client.post(self._url(), follow=True)
        flash_texts = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('Plan created for alice@test.com' in t for t in flash_texts),
            msg=f'Expected success flash, got {flash_texts!r}',
        )

    def test_post_preserves_plan_request_audit_rows(self):
        """``PlanRequest`` rows are audit data; never deleted (issue #718)."""
        # Add a second pending request so we can verify both survive.
        PlanRequest.objects.create(sprint=self.sprint, member=self.member)
        before_count = PlanRequest.objects.filter(
            sprint=self.sprint, member=self.member,
        ).count()
        self.assertEqual(before_count, 2)
        self.client.post(self._url())
        after_count = PlanRequest.objects.filter(
            sprint=self.sprint, member=self.member,
        ).count()
        self.assertEqual(after_count, 2)

    def test_after_post_member_disappears_from_inbox(self):
        self.client.post(self._url())
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        rows = response.context['pending_plan_requests']
        self.assertFalse(
            any(row['member'].pk == self.member.pk for row in rows),
            msg='Member must drop off the inbox once they have a plan.',
        )

    def test_idempotent_double_post_does_not_create_duplicate_plans(self):
        """Double-click safety: two POSTs => one plan, one enrollment."""
        self.client.post(self._url())
        response = self.client.post(self._url())
        # Still 302 to the SAME plan editor.
        plan = Plan.objects.get(sprint=self.sprint, member=self.member)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], f'/studio/plans/{plan.pk}/edit/',
        )
        self.assertEqual(
            Plan.objects.filter(
                sprint=self.sprint, member=self.member,
            ).count(),
            1,
        )
        self.assertEqual(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=self.member,
            ).count(),
            1,
        )

    def test_second_post_flashes_already_has_plan_message(self):
        self.client.post(self._url())
        response = self.client.post(self._url(), follow=True)
        flash_texts = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('already has a plan' in t for t in flash_texts),
            msg=f'Expected "already has a plan" flash, got {flash_texts!r}',
        )

    def test_race_condition_simulated_via_concurrent_create_paths(self):
        """Simulate a race where ``create_plan_for_enrollment`` returns
        an existing plan (the second concurrent path).

        We patch the service helper to behave as if the first POST won
        the race and the second POST sees ``created_now=False`` with
        the same plan. The endpoint must redirect to that same plan
        and flash the info message; it must NOT call the helper twice
        in a way that creates a duplicate.
        """
        # First POST creates the plan normally.
        self.client.post(self._url())
        existing_plan = Plan.objects.get(
            sprint=self.sprint, member=self.member,
        )
        existing_enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint, user=self.member,
        )

        # Second POST: patch the helper to simulate the race-loser
        # path. We assert (a) the response redirects to the existing
        # plan, (b) no new Plan or SprintEnrollment was materialised.
        before_plans = Plan.objects.count()
        before_enrollments = SprintEnrollment.objects.count()
        with patch(
            'studio.views.sprints.create_plan_for_enrollment',
            return_value=(existing_plan, existing_enrollment, False),
        ) as mocked:
            response = self.client.post(self._url())
        mocked.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/studio/plans/{existing_plan.pk}/edit/',
        )
        self.assertEqual(Plan.objects.count(), before_plans)
        self.assertEqual(SprintEnrollment.objects.count(), before_enrollments)


class SprintListPendingRequestsColumnTest(TestCase):
    """The sprint list table surfaces the pending-request inbox count."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint_active = _make_sprint(
            name='May 2026', slug='may-2026',
        )
        cls.sprint_quiet = Sprint.objects.create(
            name='April 2026', slug='april-2026',
            start_date=datetime.date(2026, 4, 1),
            duration_weeks=6,
        )
        # Three distinct members with pending requests in May (one has
        # multiple PlanRequest rows -- we count distinct members, not
        # rows, so this stays at 3).
        for email in ['m1@test.com', 'm2@test.com', 'm3@test.com']:
            user = User.objects.create_user(email=email, password='pw')
            PlanRequest.objects.create(sprint=cls.sprint_active, member=user)
        # An extra row for m1 to verify distinct-member counting.
        m1 = User.objects.get(email='m1@test.com')
        PlanRequest.objects.create(sprint=cls.sprint_active, member=m1)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_pending_count_is_distinct_member_count(self):
        response = self.client.get('/studio/sprints/')
        sprints_by_id = {
            sprint.pk: sprint for sprint in response.context['sprints']
        }
        self.assertEqual(
            sprints_by_id[self.sprint_active.pk].pending_request_count, 3,
        )
        self.assertEqual(
            sprints_by_id[self.sprint_quiet.pk].pending_request_count, 0,
        )

    def test_pending_count_link_targets_detail_anchor(self):
        response = self.client.get('/studio/sprints/')
        # The cell renders an anchor to ``#pending-requests`` on the
        # detail page when count > 0.
        self.assertContains(
            response,
            f'href="/studio/sprints/{self.sprint_active.pk}/'
            f'#pending-requests"',
        )

    def test_pending_count_renders_em_dash_when_zero(self):
        response = self.client.get('/studio/sprints/')
        # The quiet sprint row renders the muted em-dash. We can't grep
        # for "—" alone because Tailwind classes vary; assert on the
        # data-testid + the dash glyph together.
        self.assertContains(
            response, 'data-testid="sprint-list-pending-count"',
        )
        body = response.content.decode()
        # Find the row for the quiet sprint and assert "—" is in it
        # and no anchor link to #pending-requests for that sprint.
        quiet_anchor = (
            f'href="/studio/sprints/{self.sprint_quiet.pk}/'
            f'#pending-requests"'
        )
        self.assertNotIn(quiet_anchor, body)
        self.assertIn('—', body)

    def test_pending_requests_excludes_members_with_existing_plan(self):
        """If a request author already has a plan, they drop out of the count."""
        # Give m1 a plan -- the count for May should drop to 2.
        Plan.objects.create(
            sprint=self.sprint_active,
            member=User.objects.get(email='m1@test.com'),
        )
        response = self.client.get('/studio/sprints/')
        sprints_by_id = {
            sprint.pk: sprint for sprint in response.context['sprints']
        }
        self.assertEqual(
            sprints_by_id[self.sprint_active.pk].pending_request_count, 2,
        )


class PendingRequestsOrderingTest(TestCase):
    """Inbox rows are ordered most-recent-request-first."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = _make_sprint()
        cls.old_member = User.objects.create_user(
            email='old@test.com', password='pw',
            first_name='Old', last_name='Timer',
        )
        cls.new_member = User.objects.create_user(
            email='new@test.com', password='pw',
            first_name='Fresh', last_name='Pinger',
        )
        # Two requests; the "new" one is later.
        old = PlanRequest.objects.create(
            sprint=cls.sprint, member=cls.old_member,
        )
        PlanRequest.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=2),
        )
        PlanRequest.objects.create(sprint=cls.sprint, member=cls.new_member)

    def test_newest_request_first(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        rows = response.context['pending_plan_requests']
        member_ids_in_order = [row['member'].pk for row in rows]
        self.assertEqual(
            member_ids_in_order,
            [self.new_member.pk, self.old_member.pk],
        )
