"""Tests for the CRM-style Studio user overview (issue #494, Phase 1).

The Studio user list and detail pages already exposed everything operators
need, but the layout felt fragmented. Phase 1 wires up:

- A clickable identity area on each list row (in addition to the existing
  ``View`` action) so the email/name links to ``/studio/users/<id>/``.
- A header on the user detail page with explicit operator actions
  (``Login as user``, ``View as user``, ``Django Admin``).
- A dedicated sprint/plan section on the user detail page that links to
  the existing Studio plan/sprint surfaces instead of stuffing the list
  into the profile card.

These tests lock the structural markers (data-testid, hrefs, action
forms) so future polish passes can move CSS around without silently
dropping the contract.
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


def _row_html(html, user_pk):
    """Return the ``<tr>`` slice for a given user pk in the list HTML."""
    pattern = (
        r'<tr[^>]*data-testid="user-row-' + str(user_pk) + r'"[^>]*>'
        r'(.*?)'
        r'</tr>'
    )
    match = re.search(pattern, html, re.DOTALL)
    if match is None:
        raise AssertionError(
            f'Could not locate user-row-{user_pk} in rendered HTML.'
        )
    return match.group(0)


class UserListIdentityLinkTest(TestCase):
    """The User cell in /studio/users/ links to the detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='pw',
            first_name='Target', last_name='User',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_identity_area_is_a_link_to_user_detail(self):
        response = self.client.get('/studio/users/?q=target')
        self.assertEqual(response.status_code, 200)
        row_html = _row_html(response.content.decode(), self.target.pk)
        # The identity area is wrapped in an anchor with a stable test-id
        # pointing at the detail URL, alongside the existing user-name /
        # user-email markers (so the prior tests still pass).
        self.assertIn('data-testid="user-row-link"', row_html)
        self.assertIn(f'href="/studio/users/{self.target.pk}/"', row_html)
        self.assertIn('data-testid="user-name"', row_html)
        self.assertIn('data-testid="user-email"', row_html)

    def test_existing_view_action_is_still_present(self):
        # AC: "The row still keeps an explicit `View` action if that
        # matches the existing Studio table-action pattern." We still
        # render the secondary `View` button so operators have a
        # discoverable row action in the actions cell.
        response = self.client.get('/studio/users/?q=target')
        row_html = _row_html(response.content.decode(), self.target.pk)
        self.assertIn('data-testid="user-view-link"', row_html)
        self.assertIn(f'href="/studio/users/{self.target.pk}/"', row_html)

    def test_login_as_remains_a_post_action_not_a_get_link(self):
        # AC: impersonation must stay POST-only (no unsafe GET). The
        # login-as control is rendered as a button inside a POST form
        # against the impersonate endpoint.
        response = self.client.get('/studio/users/?q=target')
        row_html = _row_html(response.content.decode(), self.target.pk)
        self.assertIn('method="post"', row_html.lower())
        self.assertIn(
            f'action="/studio/impersonate/{self.target.pk}/"',
            row_html,
        )
        self.assertIn('Login as', row_html)


class UserDetailHeaderActionsTest(TestCase):
    """The detail page surfaces Login as / View as / Django Admin."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
            first_name='Mem', last_name='Ber',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_detail_returns_200_for_staff(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.status_code, 200)

    def test_detail_header_has_member_label_and_actions_block(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-header"')
        self.assertContains(response, 'data-testid="user-detail-actions"')

    def test_detail_header_includes_login_as_post_form(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        # Stable test-id for the impersonation submit and a POST form
        # pointing at the existing impersonate URL with CSRF.
        self.assertContains(response, 'data-testid="user-detail-impersonate"')
        self.assertContains(
            response,
            f'action="/studio/impersonate/{self.member.pk}/"',
        )
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_detail_header_includes_view_as_user_action(self):
        # The "View as user" affordance reuses the impersonation flow;
        # the UI copy makes the relationship discoverable.
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-view-as"')
        self.assertContains(response, 'View as user')

    def test_detail_header_links_to_django_admin_change_page(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-django-admin"')
        self.assertContains(
            response,
            f'href="/admin/accounts/user/{self.member.pk}/change/"',
        )

    def test_detail_does_not_add_destructive_studio_actions(self):
        # Phase 1 keeps destructive actions in Django Admin only. The
        # Studio detail page must not grow a Delete control.
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        body = response.content.decode()
        self.assertNotIn('Delete user', body)
        self.assertNotIn('Delete account', body)


class UserDetailSectionsTest(TestCase):
    """The detail page renders CRM-style sections with stable markers."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
            tags=['early-adopter'],
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_profile_membership_tags_plans_sections_present(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-profile-section"')
        self.assertContains(response, 'data-testid="user-detail-membership-section"')
        self.assertContains(response, 'data-testid="user-tags-section"')
        self.assertContains(response, 'data-testid="user-detail-plans-section"')
        self.assertContains(response, 'data-testid="member-notes-section"')

    def test_membership_section_shows_tier_and_newsletter_state(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-tier"')
        # The new section labels the newsletter chip with a plain word.
        self.assertContains(response, 'Subscribed')

    def test_existing_tags_chip_still_renders(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-tags-chips"')
        self.assertContains(response, 'early-adopter')


class UserDetailPlansSectionTest(TestCase):
    """The plans section links to existing Studio plan / sprint pages."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
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

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_no_plans_renders_empty_state_no_500(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="user-detail-plans-empty"')
        self.assertContains(response, 'No sprints or plans yet.')

    def test_member_with_plans_lists_links_to_plan_and_sprint(self):
        spring_plan = Plan.objects.create(
            member=self.member, sprint=self.spring,
        )
        summer_plan = Plan.objects.create(
            member=self.member, sprint=self.summer,
        )
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-plans-list"')
        # Each plan renders an item with a link to the plan detail.
        self.assertContains(
            response, f'href="/studio/plans/{spring_plan.pk}/"',
        )
        self.assertContains(
            response, f'href="/studio/plans/{summer_plan.pk}/"',
        )
        # The sprint link points at the existing Studio sprint detail.
        self.assertContains(
            response, f'href="/studio/sprints/{self.spring.pk}/"',
        )
        # Sprint names show in the section so staff can scan.
        self.assertContains(response, 'Spring 2026')
        self.assertContains(response, 'Summer 2026')


class UserDetailMemberNotesIntegrationTest(TestCase):
    """Member-note partial keeps internal/external split + add link."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_internal_and_external_sections_both_visible_with_distinct_labels(self):
        InterviewNote.objects.create(
            plan=None, member=self.member,
            visibility='internal', kind='intake',
            body='Internal body', created_by=self.staff,
        )
        InterviewNote.objects.create(
            plan=None, member=self.member,
            visibility='external', kind='general',
            body='External body', created_by=self.staff,
        )
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'data-testid="internal-notes"')
        self.assertContains(response, 'data-testid="external-notes"')
        self.assertContains(response, 'Internal notes (staff only)')
        self.assertContains(response, 'External notes (shareable with member)')
        self.assertContains(response, 'Internal body')
        self.assertContains(response, 'External body')

    def test_add_member_note_button_routes_to_existing_create_form(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        # Same href the partial used before: optional ?plan_id is absent
        # on the user-detail context.
        self.assertContains(response, 'data-testid="member-notes-add"')
        self.assertContains(
            response,
            f'href="/studio/users/{self.member.pk}/notes/new"',
        )

    def test_external_note_edit_link_remains_discoverable(self):
        external = InterviewNote.objects.create(
            plan=None, member=self.member,
            visibility='external', kind='general',
            body='External body', created_by=self.staff,
        )
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        # Staff can edit external notes here; member never sees this.
        self.assertContains(
            response,
            f'href="/studio/users/{self.member.pk}/notes/{external.pk}/edit"',
        )


class UserDetailAccessControlTest(TestCase):
    """Detail / impersonate / tag controls remain staff-only."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.regular = User.objects.create_user(
            email='regular@test.com', password='pw',
        )

    def test_non_staff_cannot_view_detail(self):
        self.client.login(email='regular@test.com', password='pw')
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_get_impersonate_returns_405(self):
        # Sanity: a GET to the impersonate URL stays 405 even from a
        # logged-in staff user, so the only path is the POST form on
        # the detail page (and the row).
        staff = User.objects.create_user(
            email='staff-imp@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(f'/studio/impersonate/{self.member.pk}/')
        self.assertEqual(response.status_code, 405)


class PlanDetailReusesMemberNotesPartialTest(TestCase):
    """Plan detail must keep working after the partial polish."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.spring = Sprint.objects.create(
            name='Spring 2026',
            slug='spring-2026',
            start_date=datetime.date(2026, 3, 1),
        )
        cls.spring_plan = Plan.objects.create(
            member=cls.member, sprint=cls.spring,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_plan_detail_renders_member_notes_partial(self):
        # Plan detail does NOT pass detail_user-only context like
        # member_plans, so the partial must work with the minimal
        # context (current_plan + internal_notes + external_notes).
        InterviewNote.objects.create(
            plan=self.spring_plan, member=self.member,
            visibility='internal', kind='meeting',
            body='Plan-detail note body', created_by=self.staff,
        )
        response = self.client.get(f'/studio/plans/{self.spring_plan.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Member notes')
        self.assertContains(response, 'Plan-detail note body')
        # The "Add member note" affordance prefills plan_id in the
        # plan-detail reuse path.
        self.assertContains(
            response,
            (
                f'href="/studio/users/{self.member.pk}/notes/new'
                f'?plan_id={self.spring_plan.pk}"'
            ),
        )
