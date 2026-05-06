"""Studio sprint Add member view (issue #444).

Covers the access-control matrix, GET render with the sprint locked,
POST happy path, idempotent re-add, and form-error branches. The
service-helper artefact shape (week count, blank themes, no
checkpoints) is asserted in ``plans/tests/test_services.py`` so we
do not duplicate it here.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class SprintAddMemberAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='Spring Cohort', slug='spring-cohort',
            start_date=datetime.date(2026, 5, 1),
        )

    def test_anonymous_redirects_to_login(self):
        url = f'/studio/sprints/{self.sprint.pk}/add-member'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn(f'next={url}', response['Location'])

    def test_non_staff_returns_403(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertEqual(response.status_code, 403)

    def test_non_staff_post_returns_403(self):
        self.client.login(email='member@test.com', password='pw')
        before = Plan.objects.count()
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': str(self.member.pk)},
        )
        self.assertEqual(response.status_code, 403)
        # No plan was created via the rejected request.
        self.assertEqual(Plan.objects.count(), before)

    def test_staff_get_returns_200(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertEqual(response.status_code, 200)

    def test_unknown_sprint_returns_404(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/sprints/9999999/add-member')
        self.assertEqual(response.status_code, 404)


class SprintAddMemberFormRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member_a = User.objects.create_user(
            email='alice@test.com', password='pw',
        )
        cls.member_b = User.objects.create_user(
            email='bob@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='Spring Cohort', slug='spring-cohort',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_get_renders_form_with_sprint_locked_and_no_select(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertEqual(response.status_code, 200)
        # Sprint is rendered as read-only with a hidden input -- there
        # is no <select name="sprint"> tag.
        self.assertContains(
            response,
            'data-testid="add-member-sprint-locked"',
        )
        self.assertContains(
            response,
            f'<input type="hidden" name="sprint" value="{self.sprint.pk}">',
            html=True,
        )
        # No sprint <select>.
        self.assertNotContains(response, '<select name="sprint"')

    def test_get_renders_member_picker_with_all_users(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertContains(response, 'data-testid="add-member-select"')
        self.assertContains(response, 'alice@test.com')
        self.assertContains(response, 'bob@test.com')

    def test_get_uses_existing_plan_form_template(self):
        """Reuse rule: NEVER fork the template (issue #444)."""
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertTemplateUsed(response, 'studio/plans/form.html')

    def test_get_renders_heading_with_sprint_name(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertContains(response, 'data-testid="add-member-heading"')
        self.assertContains(response, 'Spring Cohort')

    def test_form_action_url_posts_back_to_add_member(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )
        self.assertContains(
            response,
            f'action="/studio/sprints/{self.sprint.pk}/add-member"',
        )


class SprintAddMemberSubmitTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='new@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='Spring Cohort', slug='spring-cohort',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_post_creates_plan_and_enrollment_redirects_to_editor(self):
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': str(self.member.pk)},
        )
        plan = Plan.objects.get(sprint=self.sprint, member=self.member)
        # Status is 302 redirect to the editor URL with the new plan id.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], f'/studio/plans/{plan.pk}/edit/',
        )
        # SprintEnrollment row exists with enrolled_by=staff.
        enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint, user=self.member,
        )
        self.assertEqual(enrollment.enrolled_by_id, self.staff.pk)
        # Plan has 6 weeks.
        self.assertEqual(plan.weeks.count(), 6)

    def test_idempotent_re_add_does_not_duplicate_rows(self):
        # First add.
        self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': str(self.member.pk)},
        )
        plan_id = Plan.objects.get(
            sprint=self.sprint, member=self.member,
        ).pk
        # Add a checkpoint to verify data is NOT wiped.
        from plans.models import Checkpoint
        week_1 = Plan.objects.get(pk=plan_id).weeks.get(week_number=1)
        Checkpoint.objects.create(
            week=week_1, description='Read paper', position=0,
        )

        # Second add: same member.
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': str(self.member.pk)},
        )
        # Still 302 to the SAME plan editor.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'], f'/studio/plans/{plan_id}/edit/',
        )
        # No duplicate plan or enrollment.
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
        # Existing checkpoint preserved (no wipe).
        self.assertEqual(
            Checkpoint.objects.filter(week=week_1).count(),
            1,
        )

    def test_idempotent_re_add_flashes_already_enrolled(self):
        self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': str(self.member.pk)},
        )
        # ``follow=True`` so we read the flash on the redirected page.
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': str(self.member.pk)},
            follow=True,
        )
        # The redirected page is the editor; the flash should be in
        # ``response.context['messages']`` after follow.
        flash_texts = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('Already enrolled' in t for t in flash_texts),
            msg=f'Expected an Already enrolled flash, got {flash_texts!r}',
        )

    def test_post_missing_member_returns_400_with_pick_a_member(self):
        before = Plan.objects.count()
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Pick a member.', status_code=400)
        # Sprint is still rendered as locked on the re-render (the
        # form did NOT regress to the standalone create-plan UI).
        self.assertContains(
            response,
            'data-testid="add-member-sprint-locked"',
            status_code=400,
        )
        # No plan was created.
        self.assertEqual(Plan.objects.count(), before)

    def test_post_unknown_member_returns_400_with_does_not_exist(self):
        before = Plan.objects.count()
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/add-member',
            {'member': '9999999'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Selected member does not exist.',
            status_code=400,
        )
        self.assertEqual(Plan.objects.count(), before)


class SprintDetailAddMemberButtonTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='Spring Cohort', slug='spring-cohort',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_add_member_button_renders_with_link(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(response, 'data-testid="sprint-add-member-link"')
        self.assertContains(
            response,
            f'href="/studio/sprints/{self.sprint.pk}/add-member"',
        )
        # Label is "Add member".
        self.assertContains(response, '>\n        Add member\n      </a>')

    def test_add_member_button_appears_before_edit_sprint(self):
        """Add member must be LEFT of Edit sprint per the spec."""
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        body = response.content.decode()
        add_idx = body.index('data-testid="sprint-add-member-link"')
        edit_idx = body.index('Edit sprint')
        self.assertLess(
            add_idx, edit_idx,
            msg='Add member button must appear before Edit sprint',
        )
