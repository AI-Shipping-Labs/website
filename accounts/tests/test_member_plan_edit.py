"""Member-facing plan editor at ``/account/plan/<id>/edit/`` (issue #444).

Owner-only access, 404 (not 403) on visibility-leak prevention,
member-token mint, and the regression test that asserts BOTH the
Studio editor URL and the member URL render the SAME partial
(``templates/studio/plans/_editor_body.html``).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Plan, Sprint, Week

User = get_user_model()


class MemberPlanEditAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.alice_plan = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, status='draft',
        )
        cls.bob_plan = Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, status='draft',
        )

    def test_anonymous_redirects_to_login_with_next(self):
        url = f'/account/plan/{self.alice_plan.pk}/edit/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn(f'next={url}', response['Location'])

    def test_non_owner_returns_404_not_403(self):
        """Visibility-leak prevention per #440. 404 hides existence."""
        self.client.login(email='bob@test.com', password='pw')
        response = self.client.get(
            f'/account/plan/{self.alice_plan.pk}/edit/',
        )
        self.assertEqual(response.status_code, 404)

    def test_non_owner_response_does_not_leak_other_email(self):
        """The 404 page must not contain Alice's email anywhere."""
        self.client.login(email='bob@test.com', password='pw')
        response = self.client.get(
            f'/account/plan/{self.alice_plan.pk}/edit/',
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'alice@test.com', response.content)

    def test_non_existent_plan_returns_404(self):
        self.client.login(email='alice@test.com', password='pw')
        response = self.client.get('/account/plan/9999999/edit/')
        self.assertEqual(response.status_code, 404)

class MemberPlanEditTokenTest(TestCase):
    """The member editor mints a NAMED token distinct from the staff one."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def setUp(self):
        self.client.login(email='member@test.com', password='pw')

    def test_token_minted_under_member_plan_editor_name(self):
        response = self.client.get(f'/account/plan/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        tokens = Token.objects.filter(
            user=self.member, name='member-plan-editor',
        )
        self.assertEqual(tokens.count(), 1)
        token = tokens.get()
        self.assertContains(response, f'data-api-token="{token.key}"')

    def test_token_reused_across_reloads(self):
        """Reload spam must not multiply tokens (see #434 staff path)."""
        self.client.get(f'/account/plan/{self.plan.pk}/edit/')
        self.client.get(f'/account/plan/{self.plan.pk}/edit/')
        self.client.get(f'/account/plan/{self.plan.pk}/edit/')
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='member-plan-editor',
            ).count(),
            1,
        )

    def test_does_not_mint_studio_plan_editor_token_for_member(self):
        """A member opening the editor must NOT mint a staff-named token."""
        self.client.get(f'/account/plan/{self.plan.pk}/edit/')
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='studio-plan-editor',
            ).count(),
            0,
        )


class StaffEditorTokenRegressionTest(TestCase):
    """The staff editor still uses ``studio-plan-editor`` (regression)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def test_staff_editor_token_name_unchanged(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(
            f'/studio/plans/{self.plan.pk}/edit/',
        )
        self.assertEqual(response.status_code, 200)
        # Staff path mints the studio-named token, not the member one.
        self.assertEqual(
            Token.objects.filter(
                user=self.staff, name='studio-plan-editor',
            ).count(),
            1,
        )
        self.assertEqual(
            Token.objects.filter(
                user=self.staff, name='member-plan-editor',
            ).count(),
            0,
        )


class SamePartialRegressionTest(TestCase):
    """Both editor URLs MUST render the same partial.

    This is the spec's "no parallel editor" guard. If a future change
    accidentally forks the markup into a separate template, this test
    fails immediately.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=3,
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )
        Week.objects.create(plan=cls.plan, week_number=1, position=0)
        Week.objects.create(plan=cls.plan, week_number=2, position=1)

    def test_studio_editor_uses_partial(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(
            f'/studio/plans/{self.plan.pk}/edit/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/plans/edit.html')
        self.assertTemplateUsed(response, 'studio/plans/_editor_body.html')

    def test_member_editor_uses_same_partial(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(
            f'/account/plan/{self.plan.pk}/edit/',
        )
        self.assertEqual(response.status_code, 200)
        # Member shell extends the public base.html, NOT studio/base.html.
        self.assertTemplateUsed(response, 'account/plan_edit.html')
        # Same partial -- the regression guard.
        self.assertTemplateUsed(response, 'studio/plans/_editor_body.html')

    def test_member_editor_renders_editor_root_with_data_attributes(self):
        """Real bug guard: missing partial would silently render an
        empty page. Asserting the editor root and a known sibling
        element from the partial proves the include actually fired.
        """
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(
            f'/account/plan/{self.plan.pk}/edit/',
        )
        self.assertContains(response, 'id="plan-editor"')
        self.assertContains(
            response,
            f'data-plan-id="{self.plan.pk}"',
        )
        self.assertContains(response, 'data-testid="summary-block"')
        self.assertContains(response, 'data-testid="weeks-column"')

    def test_member_editor_does_not_extend_studio_base(self):
        """Member-facing pages render the public chrome.

        The Studio sidebar must not appear on a member URL. Asserting
        on a Studio-only marker (the sidebar's plans link) catches a
        regression where the member shell accidentally extends
        ``studio/base.html``.
        """
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(
            f'/account/plan/{self.plan.pk}/edit/',
        )
        self.assertNotContains(response, 'href="/studio/plans/"')
        self.assertNotContains(
            response,
            f'href="/studio/users/{self.member.pk}/"',
        )

    def test_member_editor_renders_member_email_without_studio_link(self):
        """The header still names the member, but does not link to Studio."""
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(
            f'/account/plan/{self.plan.pk}/edit/',
        )
        self.assertContains(response, 'member@test.com')
