"""Tests for the owner-only my-plan view and visibility toggle (issue #440).

Owner identity, valid POST values, and side-effect-free rejection of
unauthorized / invalid requests are pinned down here. Per Rule 12 of
``_docs/testing-guidelines.md``, every "rejection" test also asserts
that the database row was NOT mutated.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint

User = get_user_model()


class MyPlanDetailAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.other_sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        cls.owner_plan = Plan.objects.create(
            member=cls.owner, sprint=cls.sprint, visibility='private',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.teammate, sprint=cls.sprint, visibility='cohort',
        )

    def test_my_plan_detail_owner_returns_200(self):
        self.client.force_login(self.owner)
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.owner_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_my_plan_detail_non_owner_returns_404(self):
        self.client.force_login(self.teammate)
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.owner_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_my_plan_detail_anonymous_redirects_to_login(self):
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.owner_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_my_plan_detail_wrong_sprint_slug_returns_404(self):
        self.client.force_login(self.owner)
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.other_sprint.slug,
                'plan_id': self.owner_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_old_account_plan_urls_are_not_compatible(self):
        self.client.force_login(self.owner)
        detail_response = self.client.get(f'/account/plan/{self.owner_plan.pk}')
        edit_response = self.client.get(
            f'/account/plan/{self.owner_plan.pk}/edit/',
        )
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(edit_response.status_code, 404)


class VisibilityToggleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        # Need fresh ``Plan`` rows per test to assert mutation, so this
        # one stays in setUp not setUpTestData.
        self.owner = User.objects.create_user(
            email='owner@test.com', password='pw',
        )
        self.plan = Plan.objects.create(
            member=self.owner, sprint=self.sprint, visibility='private',
        )

    def _toggle_url(self):
        return reverse(
            'update_plan_visibility',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

    def test_visibility_toggle_to_cohort(self):
        self.client.force_login(self.owner)
        response = self.client.post(self._toggle_url(), {'visibility': 'cohort'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
            ),
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'cohort')

    def test_visibility_toggle_to_private(self):
        self.plan.visibility = 'cohort'
        self.plan.save(update_fields=['visibility'])
        self.client.force_login(self.owner)
        response = self.client.post(self._toggle_url(), {'visibility': 'private'})
        self.assertEqual(response.status_code, 302)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')

    def test_visibility_toggle_rejects_invalid_value(self):
        """Invalid value -> 400 and the row is NOT mutated."""
        self.client.force_login(self.owner)
        response = self.client.post(self._toggle_url(), {'visibility': 'public'})
        self.assertEqual(response.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')  # unchanged

    def test_visibility_toggle_rejects_missing_value(self):
        self.client.force_login(self.owner)
        response = self.client.post(self._toggle_url(), {})
        self.assertEqual(response.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')  # unchanged

    def test_visibility_toggle_non_owner_returns_404(self):
        other = User.objects.create_user(
            email='other@test.com', password='pw',
        )
        Plan.objects.create(
            member=other, sprint=self.sprint, visibility='cohort',
        )
        self.client.force_login(other)
        response = self.client.post(self._toggle_url(), {'visibility': 'cohort'})
        self.assertEqual(response.status_code, 404)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')  # unchanged

    def test_visibility_toggle_anonymous_redirects_and_does_nothing(self):
        response = self.client.post(self._toggle_url(), {'visibility': 'cohort'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')  # unchanged

    def test_visibility_toggle_get_not_allowed(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._toggle_url())
        self.assertEqual(response.status_code, 405)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')  # unchanged

    def test_visibility_toggle_no_flash_message_added(self):
        """Issue #583: the toggle now shows inline feedback via JS, so the
        server no longer pushes a one-shot ``messages.success`` flash on
        success. Rendering both would re-introduce the "notifications top
        and bottom" duplicate the issue was filed to fix.
        """
        self.client.force_login(self.owner)
        response = self.client.post(
            self._toggle_url(), {'visibility': 'cohort'}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 0)

    def test_visibility_toggle_returns_json_when_accept_json(self):
        """JS toggle path: ``Accept: application/json`` -> JSON body.

        The new inline switch fetches with ``Accept: application/json``
        so it can react to success/failure without following a redirect.
        """
        self.client.force_login(self.owner)
        response = self.client.post(
            self._toggle_url(),
            {'visibility': 'cohort'},
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response.json(), {'visibility': 'cohort'})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'cohort')

    def test_visibility_toggle_json_invalid_value_returns_400(self):
        """JS path rejects invalid values the same way the HTML path does."""
        self.client.force_login(self.owner)
        response = self.client.post(
            self._toggle_url(),
            {'visibility': 'public'},
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'private')
