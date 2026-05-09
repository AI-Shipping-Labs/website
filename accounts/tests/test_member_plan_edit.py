"""Member sprint plan edit route regressions for issue #548."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
        cls.sprint = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.other_sprint = Sprint.objects.create(
            name='Other', slug='other',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.alice_plan = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, status='draft',
        )
        Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, status='draft',
        )

    def _edit_url(self, sprint=None):
        return reverse(
            'my_plan_edit',
            kwargs={
                'sprint_slug': (sprint or self.sprint).slug,
                'plan_id': self.alice_plan.pk,
            },
        )

    def test_anonymous_redirects_to_login_with_next(self):
        url = self._edit_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn(f'next={url}', response['Location'])

    def test_owner_can_open_sprint_scoped_edit_workspace(self):
        self.client.login(email='alice@test.com', password='pw')
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'plans/my_plan_detail.html')
        self.assertContains(response, 'data-testid="member-plan"')
        self.assertContains(response, 'data-testid="plan-weeks"')

    def test_non_owner_returns_404_not_403(self):
        self.client.login(email='bob@test.com', password='pw')
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 404)

    def test_wrong_sprint_slug_returns_404(self):
        self.client.login(email='alice@test.com', password='pw')
        response = self.client.get(self._edit_url(self.other_sprint))
        self.assertEqual(response.status_code, 404)

    def test_old_account_edit_url_is_not_compatible(self):
        self.client.login(email='alice@test.com', password='pw')
        response = self.client.get(f'/account/plan/{self.alice_plan.pk}/edit/')
        self.assertEqual(response.status_code, 404)


class MemberPlanEditTokenTest(TestCase):
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

    def _edit_url(self):
        return reverse(
            'my_plan_edit',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def test_token_minted_under_member_plan_editor_name(self):
        response = self.client.get(self._edit_url())
        self.assertEqual(response.status_code, 200)
        tokens = Token.objects.filter(
            user=self.member, name='member-plan-editor',
        )
        self.assertEqual(tokens.count(), 1)
        token = tokens.get()
        self.assertContains(response, f'data-api-token="{token.key}"')

    def test_token_reused_across_reloads(self):
        self.client.get(self._edit_url())
        self.client.get(self._edit_url())
        self.client.get(self._edit_url())
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='member-plan-editor',
            ).count(),
            1,
        )

    def test_does_not_mint_studio_plan_editor_token_for_member(self):
        self.client.get(self._edit_url())
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='studio-plan-editor',
            ).count(),
            0,
        )


class StaffEditorRegressionTest(TestCase):
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

    def test_staff_editor_still_uses_studio_template_and_token(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/plans/edit.html')
        self.assertTemplateUsed(response, 'studio/plans/_editor_body.html')
        self.assertContains(response, 'id="plan-editor"')
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
