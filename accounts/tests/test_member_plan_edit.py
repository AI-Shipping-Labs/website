"""Member sprint plan edit route regressions for issue #548."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Token
from plans.models import Checkpoint, Plan, Sprint, Week

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

    def test_anonymous_following_legacy_edit_url_lands_on_login(self):
        """Issue #583: ``/edit`` is now a 301 to the unified workspace,
        which is itself ``@login_required``. An anonymous user therefore
        ends up at the login page with ``next=`` pointing at the
        canonical workspace URL (not the legacy /edit URL)."""
        url = self._edit_url()
        canonical = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan.pk,
            },
        )
        response = self.client.get(url, follow=True)
        # The final hop is the login page.
        self.assertEqual(response.status_code, 200)
        # The intermediate hop is the 301.
        self.assertEqual(response.redirect_chain[0][1], 301)
        self.assertEqual(response.redirect_chain[0][0], canonical)
        self.assertIn('/accounts/login/', response.redirect_chain[-1][0])
        self.assertIn(f'next={canonical}', response.redirect_chain[-1][0])

    def test_owner_following_legacy_edit_url_lands_on_workspace(self):
        """Issue #583: legacy ``/edit`` URL is a 301 to the unified
        workspace. Following the redirect lands on the same template the
        new workspace serves, with the inline-edit markup intact."""
        self.client.login(email='alice@test.com', password='pw')
        response = self.client.get(self._edit_url(), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[0][1], 301)
        self.assertTemplateUsed(response, 'plans/my_plan_detail.html')
        self.assertContains(response, 'data-testid="member-plan"')
        self.assertContains(response, 'data-testid="plan-weeks"')

    def test_non_owner_following_legacy_edit_url_returns_404(self):
        """Issue #583: the 301 lands on ``my_plan_detail`` which still
        enforces owner-only access -- a non-owner gets 404 after the
        redirect, not 200 or 403."""
        self.client.login(email='bob@test.com', password='pw')
        response = self.client.get(self._edit_url(), follow=True)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.redirect_chain[0][1], 301)

    def test_wrong_sprint_slug_following_redirect_returns_404(self):
        """Issue #583: the 301 keeps the same (mismatched) sprint slug,
        and the unified workspace then rejects the wrong-sprint combo."""
        self.client.login(email='alice@test.com', password='pw')
        response = self.client.get(
            self._edit_url(self.other_sprint), follow=True,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.redirect_chain[0][1], 301)

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

    def test_member_workspace_does_not_mint_or_expose_api_token(self):
        # Issue #583: /edit is a 301 to the unified workspace; follow
        # the redirect so we exercise the same render path.
        response = self.client.get(self._edit_url(), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-api-token=')
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='member-plan-editor',
            ).count(),
            0,
        )

    def test_reloads_do_not_create_member_plan_editor_token(self):
        self.client.get(self._edit_url(), follow=True)
        self.client.get(self._edit_url(), follow=True)
        self.client.get(self._edit_url(), follow=True)
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='member-plan-editor',
            ).count(),
            0,
        )

    def test_does_not_mint_studio_plan_editor_token_for_member(self):
        self.client.get(self._edit_url(), follow=True)
        self.assertEqual(
            Token.objects.filter(
                user=self.member, name='studio-plan-editor',
            ).count(),
            0,
        )


class MemberPlanEditSessionWriteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )
        cls.week = Week.objects.create(
            plan=cls.plan, week_number=1, position=0,
        )
        cls.checkpoint = Checkpoint.objects.create(
            week=cls.week, description='Before', position=0,
        )

    def _patch_checkpoint(self, client, csrf_token=None):
        headers = {}
        if csrf_token:
            headers['HTTP_X_CSRFTOKEN'] = csrf_token
        return client.patch(
            f'/api/checkpoints/{self.checkpoint.pk}',
            data=json.dumps({'description': 'After'}),
            content_type='application/json',
            **headers,
        )

    def test_member_session_write_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.login(email='member@test.com', password='pw')

        response = self._patch_checkpoint(client)
        self.assertEqual(response.status_code, 403)

        self.checkpoint.refresh_from_db()
        self.assertEqual(self.checkpoint.description, 'Before')

    def test_member_session_write_with_csrf_updates_owned_plan(self):
        client = Client(enforce_csrf_checks=True)
        client.login(email='member@test.com', password='pw')
        # Issue #583: /edit is a 301 -- follow it so the workspace
        # actually renders and sets the csrftoken cookie.
        page = client.get(
            reverse(
                'my_plan_edit',
                kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
            ),
            follow=True,
        )
        csrf_token = page.cookies['csrftoken'].value

        response = self._patch_checkpoint(client, csrf_token=csrf_token)
        self.assertEqual(response.status_code, 200)

        self.checkpoint.refresh_from_db()
        self.assertEqual(self.checkpoint.description, 'After')
        self.assertFalse(Token.objects.filter(user=self.member).exists())


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
