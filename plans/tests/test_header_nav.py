"""Tests for sprint shortcuts in the public site header."""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class HeaderPlanLinkTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def _sprint(
        self,
        slug,
        *,
        start_offset,
        duration_weeks=4,
        status='active',
    ):
        return Sprint.objects.create(
            name=slug.replace('-', ' ').title(),
            slug=slug,
            start_date=self.today + datetime.timedelta(days=start_offset),
            duration_weeks=duration_weeks,
            status=status,
        )

    def _create_plan(self, sprint, *, member=None, created_days_ago=None):
        plan = Plan.objects.create(
            member=member or self.user,
            sprint=sprint,
            visibility='private',
        )
        if created_days_ago is not None:
            Plan.objects.filter(pk=plan.pk).update(
                created_at=timezone.now() - datetime.timedelta(
                    days=created_days_ago,
                ),
            )
            plan.refresh_from_db()
        return plan

    def _get_home(self, user=None):
        self.client.force_login(user or self.user)
        return self.client.get('/')

    @staticmethod
    def _shortcut_links(response):
        html = response.content.decode()
        links = {}
        for test_id in ('header-plan-link', 'mobile-header-plan-link'):
            match = re.search(
                rf'<a (?=[^>]*data-testid="{test_id}")'
                r'(?=[^>]*href="([^"]+)")[^>]*>(.*?)</a>',
                html,
                flags=re.S,
            )
            if match:
                links[test_id] = (
                    match.group(1),
                    re.sub(r'\s+', ' ', match.group(2)).strip(),
                )
        return links

    def assertShortcut(self, response, *, label, href):
        links = self._shortcut_links(response)
        self.assertEqual(
            links,
            {
                'header-plan-link': (href, label),
                'mobile-header-plan-link': (href, label),
            },
        )

    def assertNoShortcut(self, response):
        self.assertNotContains(response, 'data-testid="header-plan-link"')
        self.assertNotContains(response, 'data-testid="mobile-header-plan-link"')

    def test_authenticated_user_without_plan_does_not_see_plan_link(self):
        response = self._get_home()
        self.assertNoShortcut(response)

    def test_current_plan_beats_newer_created_ended_plan(self):
        current = self._sprint('current-plan', start_offset=-7)
        ended = self._sprint(
            'ended-plan',
            start_offset=-70,
            duration_weeks=4,
            status='completed',
        )
        current_plan = self._create_plan(current, created_days_ago=30)
        ended_plan = self._create_plan(ended, created_days_ago=1)

        response = self._get_home()

        expected_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': current.slug, 'plan_id': current_plan.pk},
        )
        self.assertShortcut(response, label='Plan', href=expected_href)
        shortcut_hrefs = [href for href, _label in self._shortcut_links(response).values()]
        self.assertNotIn(
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': ended.slug, 'plan_id': ended_plan.pk},
            ),
            shortcut_hrefs,
        )

    def test_upcoming_plan_beats_newer_created_ended_plan_when_no_current_plan(self):
        upcoming = self._sprint('upcoming-plan', start_offset=14)
        ended = self._sprint(
            'ended-upcoming-plan',
            start_offset=-70,
            duration_weeks=4,
            status='completed',
        )
        upcoming_plan = self._create_plan(upcoming, created_days_ago=30)
        ended_plan = self._create_plan(ended, created_days_ago=1)

        response = self._get_home()

        expected_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': upcoming.slug, 'plan_id': upcoming_plan.pk},
        )
        self.assertShortcut(response, label='Plan', href=expected_href)
        shortcut_hrefs = [href for href, _label in self._shortcut_links(response).values()]
        self.assertNotIn(
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': ended.slug, 'plan_id': ended_plan.pk},
            ),
            shortcut_hrefs,
        )

    def test_multiple_current_plans_use_latest_sprint_start_before_created_at(self):
        older_current = self._sprint('older-current', start_offset=-14)
        newer_current = self._sprint('newer-current', start_offset=-7)
        old_start_new_created = self._create_plan(
            older_current,
            created_days_ago=1,
        )
        latest_start_plan = self._create_plan(
            newer_current,
            created_days_ago=30,
        )

        response = self._get_home()

        expected_href = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': newer_current.slug,
                'plan_id': latest_start_plan.pk,
            },
        )
        self.assertShortcut(response, label='Plan', href=expected_href)
        shortcut_hrefs = [href for href, _label in self._shortcut_links(response).values()]
        self.assertNotIn(
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': older_current.slug,
                    'plan_id': old_start_new_created.pk,
                },
            ),
            shortcut_hrefs,
        )

    def test_multiple_upcoming_plans_use_earliest_sprint_start(self):
        next_sprint = self._sprint('next-upcoming', start_offset=7)
        later_sprint = self._sprint('later-upcoming', start_offset=30)
        later_new_created = self._create_plan(later_sprint, created_days_ago=1)
        next_plan = self._create_plan(next_sprint, created_days_ago=30)

        response = self._get_home()

        expected_href = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': next_sprint.slug, 'plan_id': next_plan.pk},
        )
        self.assertShortcut(response, label='Plan', href=expected_href)
        shortcut_hrefs = [href for href, _label in self._shortcut_links(response).values()]
        self.assertNotIn(
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': later_sprint.slug,
                    'plan_id': later_new_created.pk,
                },
            ),
            shortcut_hrefs,
        )

    def test_ended_plans_with_current_enrollment_link_to_cohort_board(self):
        ended = self._sprint(
            'ended-with-current-enrollment',
            start_offset=-70,
            duration_weeks=4,
            status='completed',
        )
        current = self._sprint('current-enrollment', start_offset=-7)
        self._create_plan(ended, created_days_ago=1)
        SprintEnrollment.objects.create(sprint=current, user=self.user)

        response = self._get_home()

        expected_href = reverse(
            'cohort_board', kwargs={'sprint_slug': current.slug},
        )
        self.assertShortcut(response, label='Cohort', href=expected_href)

    def test_upcoming_enrollment_without_plan_links_to_sprint_detail(self):
        ended = self._sprint(
            'ended-with-upcoming-enrollment',
            start_offset=-70,
            duration_weeks=4,
            status='completed',
        )
        upcoming = self._sprint('upcoming-enrollment', start_offset=14)
        self._create_plan(ended, created_days_ago=1)
        SprintEnrollment.objects.create(sprint=upcoming, user=self.user)

        response = self._get_home()

        expected_href = reverse(
            'sprint_detail', kwargs={'sprint_slug': upcoming.slug},
        )
        self.assertShortcut(response, label='Sprint', href=expected_href)

    def test_ended_only_state_hides_personal_shortcut(self):
        ended = self._sprint(
            'ended-only',
            start_offset=-70,
            duration_weeks=4,
            status='completed',
        )
        self._create_plan(ended, created_days_ago=1)

        response = self._get_home()

        self.assertNoShortcut(response)

    def test_newsletter_only_user_keeps_trimmed_header_even_with_stale_plan(self):
        newsletter = User.objects.create_user(
            email='newsletter@test.com',
            password='pw',
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
        )
        current = self._sprint('newsletter-current-plan', start_offset=-7)
        self._create_plan(current, member=newsletter)

        self.client.force_login(newsletter)
        response = self.client.get('/account/')
        self.assertEqual(response.status_code, 200)

        self.assertNoShortcut(response)
        self.assertNotContains(response, 'id="notification-bell-btn"')
        self.assertNotContains(response, 'href="/account/#profile"')

    def test_staff_shortcut_only_uses_staff_users_own_member_state(self):
        staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        other = User.objects.create_user(email='other@test.com', password='pw')
        current = self._sprint('other-current-plan', start_offset=-7)
        self._create_plan(current, member=other)

        response = self._get_home(staff)

        self.assertNoShortcut(response)
        self.assertContains(response, reverse('studio_dashboard'))
        self.assertContains(response, 'data-testid="header-admin-role-badge"')

    def test_cancelled_sprints_are_ignored_for_shortcut_selection(self):
        cancelled = self._sprint(
            'cancelled-current-plan',
            start_offset=-7,
            status='cancelled',
        )
        current = self._sprint('active-current-enrollment', start_offset=-7)
        self._create_plan(cancelled, created_days_ago=1)
        SprintEnrollment.objects.create(sprint=current, user=self.user)

        response = self._get_home()

        expected_href = reverse(
            'cohort_board', kwargs={'sprint_slug': current.slug},
        )
        self.assertShortcut(response, label='Cohort', href=expected_href)

    def test_my_plan_detail_renders_view_cohort_board_cta(self):
        sprint = self._sprint('my-plan-detail-sprint', start_offset=-7)
        plan = Plan.objects.create(
            member=self.user, sprint=sprint, visibility='cohort',
        )
        self.client.force_login(self.user)
        url = reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        cohort_url = reverse(
            'cohort_board', kwargs={'sprint_slug': sprint.slug},
        )
        self.assertContains(response, f'href="{cohort_url}"')
        self.assertContains(response, 'data-testid="view-cohort-board-cta"')
        # Issue #583: the legacy "Edit workspace" CTA was removed because
        # the workspace itself is the editor (inline edits live below).
        # The old /edit URL still resolves but only via a 301 redirect.
        self.assertNotContains(response, 'data-testid="my-plan-edit-cta"')
