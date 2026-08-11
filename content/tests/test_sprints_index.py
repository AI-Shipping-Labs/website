import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


def _active_sprint_start():
    return timezone.localdate() - datetime.timedelta(days=14)


def _create_sprint(
    name,
    slug,
    *,
    start_date=None,
    duration_weeks=4,
    status='active',
    min_tier_level=20,
):
    return Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date or _active_sprint_start(),
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )


def _section_markup(response, section_key):
    content = response.content.decode()
    marker = f'data-testid="sprints-section-{section_key}"'
    start = content.index(marker)
    next_markers = [
        content.find(f'data-testid="sprints-section-{key}"', start + 1)
        for key in ('current', 'future', 'past')
    ]
    next_markers = [index for index in next_markers if index != -1]
    next_start = min(next_markers) if next_markers else len(content)
    return content[start:next_start]


def _sprint_card_markup(response, slug):
    """Return the single fully-clickable sprint card for ``slug``."""
    content = response.content.decode()
    needle = f'href="/sprints/{slug}"'
    anchor = content.index(needle)
    start = content.rindex('<article', 0, anchor)
    end = content.index('</article>', anchor) + len('</article>')
    return content[start:end]


class SprintsIndexTest(TestCase):
    @staticmethod
    def _membership_queries(queries):
        return [
            query['sql']
            for query in queries
            if '"plans_plan"' in query['sql']
            or '"plans_sprintenrollment"' in query['sql']
        ]

    def test_route_returns_200_for_anonymous_users(self):
        response = self.client.get('/sprints')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/sprints_index.html')
        self.assertContains(response, 'Community Sprints')
        self.assertContains(response, 'data-testid="sprints-index-page"')

    def test_visible_sprints_render_in_current_future_past_sections(self):
        today = timezone.localdate()
        current = _create_sprint(
            'Current Sprint',
            'current-sprint',
            start_date=today - datetime.timedelta(days=7),
        )
        future = _create_sprint(
            'Future Sprint',
            'future-sprint',
            start_date=today + datetime.timedelta(days=14),
        )
        past = _create_sprint(
            'Past Sprint',
            'past-sprint',
            start_date=today - datetime.timedelta(days=42),
        )

        response = self.client.get('/sprints')
        content = response.content.decode()

        current_index = content.index('Current sprint')
        future_index = content.index('Future sprint')
        past_index = content.index('Past sprint')
        self.assertLess(current_index, future_index)
        self.assertLess(future_index, past_index)

        current_section = _section_markup(response, 'current')
        future_section = _section_markup(response, 'future')
        past_section = _section_markup(response, 'past')
        self.assertIn(current.name, current_section)
        self.assertNotIn(future.name, current_section)
        self.assertNotIn(past.name, current_section)
        self.assertIn(future.name, future_section)
        self.assertIn(past.name, past_section)

    def test_date_grouping_uses_timezone_localdate_and_derived_end_date(self):
        today = datetime.date(2026, 6, 30)
        active_but_finished = _create_sprint(
            'Active Status Finished Dates',
            'active-status-finished-dates',
            start_date=today - datetime.timedelta(days=35),
            duration_weeks=4,
            status='active',
        )
        completed_future = _create_sprint(
            'Completed Status Future Dates',
            'completed-status-future-dates',
            start_date=today + datetime.timedelta(days=7),
            status='completed',
        )
        ending_today = _create_sprint(
            'Ending Today',
            'ending-today',
            start_date=today - datetime.timedelta(days=28),
            duration_weeks=4,
            status='completed',
        )

        with patch(
            'content.views.pages.timezone.localdate',
            return_value=today,
        ):
            response = self.client.get('/sprints')

        self.assertIn(active_but_finished.name, _section_markup(response, 'past'))
        self.assertIn(completed_future.name, _section_markup(response, 'future'))
        self.assertIn(ending_today.name, _section_markup(response, 'current'))

    def test_visibility_rules_hide_drafts_for_non_staff_and_cancelled_for_all(self):
        today = timezone.localdate()
        active = _create_sprint('Active Sprint', 'active-sprint')
        completed = _create_sprint(
            'Completed Sprint',
            'completed-sprint',
            start_date=today - datetime.timedelta(days=42),
            status='completed',
        )
        _create_sprint('Draft Sprint', 'draft-sprint', status='draft')
        _create_sprint('Cancelled Sprint', 'cancelled-sprint', status='cancelled')
        member = User.objects.create_user(email='member545@example.com', password='pw')

        anonymous_response = self.client.get('/sprints')
        self.assertContains(anonymous_response, active.name)
        self.assertContains(anonymous_response, completed.name)
        self.assertNotContains(anonymous_response, 'Draft Sprint')
        self.assertNotContains(anonymous_response, 'Cancelled Sprint')

        self.client.force_login(member)
        member_response = self.client.get('/sprints')
        self.assertContains(member_response, active.name)
        self.assertContains(member_response, completed.name)
        self.assertNotContains(member_response, 'Draft Sprint')
        self.assertNotContains(member_response, 'Cancelled Sprint')

        self.client.logout()
        staff = User.objects.create_user(
            email='staff545@example.com',
            password='pw',
            is_staff=True,
        )
        self.client.force_login(staff)
        staff_response = self.client.get('/sprints')
        self.assertContains(staff_response, 'Draft Sprint')
        self.assertNotContains(staff_response, 'Cancelled Sprint')

    def test_sections_sort_by_lifecycle_rules(self):
        today = timezone.localdate()
        current_later = _create_sprint(
            'Beta Current',
            'beta-current',
            start_date=today - datetime.timedelta(days=6),
        )
        current_earlier = _create_sprint(
            'Alpha Current',
            'alpha-current',
            start_date=today - datetime.timedelta(days=10),
        )
        future_later = _create_sprint(
            'Later Future',
            'later-future',
            start_date=today + datetime.timedelta(days=21),
        )
        future_soon = _create_sprint(
            'Soon Future',
            'soon-future',
            start_date=today + datetime.timedelta(days=7),
        )
        past_older = _create_sprint(
            'Older Past',
            'older-past',
            start_date=today - datetime.timedelta(days=70),
            duration_weeks=4,
            status='completed',
        )
        past_recent = _create_sprint(
            'Recent Past',
            'recent-past',
            start_date=today - datetime.timedelta(days=35),
            duration_weeks=4,
            status='completed',
        )

        response = self.client.get('/sprints')

        current_section = _section_markup(response, 'current')
        self.assertLess(
            current_section.index(current_earlier.name),
            current_section.index(current_later.name),
        )
        future_section = _section_markup(response, 'future')
        self.assertLess(
            future_section.index(future_soon.name),
            future_section.index(future_later.name),
        )
        past_section = _section_markup(response, 'past')
        self.assertLess(
            past_section.index(past_recent.name),
            past_section.index(past_older.name),
        )

    def test_section_headings_use_singular_and_plural_and_empty_messages(self):
        today = timezone.localdate()
        _create_sprint(
            'Only Future',
            'only-future',
            start_date=today + datetime.timedelta(days=14),
        )
        _create_sprint(
            'Only Past',
            'only-past',
            start_date=today - datetime.timedelta(days=42),
            status='completed',
        )

        response = self.client.get('/sprints')

        self.assertContains(response, 'Current sprints')
        self.assertContains(response, 'No sprint is running right now.')
        self.assertContains(response, 'Future sprint')
        self.assertContains(response, 'Past sprint')
        self.assertNotContains(response, 'data-testid="sprints-empty"')

    def test_multiple_visible_sprints_use_plural_heading(self):
        today = timezone.localdate()
        _create_sprint(
            'Alpha Current',
            'alpha-current',
            start_date=today - datetime.timedelta(days=14),
        )
        _create_sprint(
            'Beta Current',
            'beta-current',
            start_date=today - datetime.timedelta(days=7),
        )

        response = self.client.get('/sprints')

        self.assertContains(response, 'Current sprints')
        current_section = _section_markup(response, 'current')
        self.assertIn('Alpha Current', current_section)
        self.assertIn('Beta Current', current_section)

    def test_empty_state_renders_when_no_visible_sprints_exist(self):
        _create_sprint('Draft Sprint', 'draft-sprint', status='draft')
        _create_sprint('Cancelled Sprint', 'cancelled-sprint', status='cancelled')

        response = self.client.get('/sprints')

        self.assertContains(response, 'data-testid="sprints-empty"')
        self.assertContains(response, 'Next sprint coming soon')
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/workshops"')
        self.assertNotContains(response, 'data-testid="sprints-section-current"')
        self.assertNotContains(response, 'data-testid="sprints-sprint-card"')

    def test_sprint_card_data_renders_for_anonymous_users(self):
        sprint = _create_sprint(
            'May Shipping Sprint',
            'may-shipping-sprint',
            duration_weeks=4,
            min_tier_level=20,
        )

        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertTemplateUsed(response, 'content/_content_card.html')
        self.assertTemplateUsed(response, 'content/_sprint_card_badges.html')
        self.assertTemplateUsed(response, 'content/_sprint_card_body.html')
        self.assertContains(response, 'mx-auto max-w-3xl')
        self.assertContains(response, 'border-b border-border/70')
        self.assertNotContains(response, 'shadow-sm')
        self.assertContains(response, 'data-testid="sprints-sprint-card"')
        self.assertContains(response, sprint.name)
        self.assertContains(response, 'Active')
        self.assertContains(response, '(4 weeks)')
        self.assertContains(response, 'Main or above')
        self.assertContains(response, 'data-testid="sprints-sprint-tier"')
        self.assertContains(response, 'data-component="member-badge"')
        self.assertContains(response, 'Sprint window')
        self.assertContains(
            response,
            'A sprint is a time-bound shipping cohort with project structure',
        )
        self.assertContains(response, 'data-testid="sprints-sprint-context"')
        self.assertNotIn('Joining requires', card)
        self.assertNotIn('Log in to join', card)
        self.assertNotIn('data-testid="sprints-sprint-cta"', card)
        self.assertIn(
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": sprint.slug})}"',
            card,
        )

    def test_below_tier_member_card_links_to_sprint_detail(self):
        sprint = _create_sprint(
            'Premium Sprint',
            'premium-sprint',
            min_tier_level=30,
        )
        member = User.objects.create_user(email='free545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn('Premium', card)
        self.assertIn(f'href="/sprints/{sprint.slug}"', card)
        self.assertNotIn('Upgrade to Premium', card)
        self.assertNotIn(f'href="{reverse("pricing")}"', card)

    def test_enrolled_member_with_plan_keeps_badge_and_detail_link(self):
        sprint = _create_sprint('Main Sprint', 'main-sprint')
        member = User.objects.create_user(email='main545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = Plan.objects.create(member=member, sprint=sprint, visibility='cohort')

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn("You're enrolled", card)
        self.assertIn(f'href="/sprints/{sprint.slug}"', card)
        self.assertNotIn('Open my plan', card)
        self.assertNotIn(
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
            card,
        )

    def test_enrolled_member_without_plan_keeps_badge_and_detail_link(self):
        sprint = _create_sprint('Board Sprint', 'board-sprint')
        member = User.objects.create_user(email='board545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn("You're enrolled", card)
        self.assertIn(f'href="/sprints/{sprint.slug}"', card)
        self.assertNotIn('Open cohort board', card)
        self.assertNotIn(
            reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
            card,
        )

    def test_sprint_detail_route_still_resolves_after_index_route(self):
        sprint = _create_sprint('Detail Sprint', 'detail-sprint')

        response = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'plans/sprint_detail.html')
        self.assertContains(response, 'data-testid="sprint-detail-name"')
        self.assertContains(response, 'mx-auto max-w-3xl')

    def test_member_membership_queries_are_constant_across_all_sections(self):
        today = timezone.localdate()
        current = _create_sprint(
            'Query Current', 'query-current',
            start_date=today - datetime.timedelta(days=7),
        )
        future = _create_sprint(
            'Query Future', 'query-future',
            start_date=today + datetime.timedelta(days=7),
        )
        _create_sprint(
            'Query Past', 'query-past',
            start_date=today - datetime.timedelta(days=42),
            status='completed',
        )
        member = User.objects.create_user(
            email='query-main@example.com', password='pw',
        )
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        Plan.objects.create(
            member=member, sprint=current, visibility='cohort',
        )
        SprintEnrollment.objects.create(sprint=future, user=member)
        self.client.force_login(member)

        with CaptureQueriesContext(connection) as first_queries:
            response = self.client.get('/sprints')

        membership_queries = self._membership_queries(first_queries)
        self.assertEqual(len(membership_queries), 2, membership_queries)
        self.assertEqual(
            sum('FROM "plans_plan"' in sql for sql in membership_queries), 1,
        )
        self.assertEqual(
            sum(
                'FROM "plans_sprintenrollment"' in sql
                for sql in membership_queries
            ),
            1,
        )
        self.assertContains(response, "You're enrolled", count=2)
        self.assertNotContains(response, 'Open my plan')
        self.assertNotContains(response, 'Open cohort board')
        self.assertNotContains(response, 'data-testid="sprints-sprint-cta"')

        for index in range(5):
            _create_sprint(
                f'Extra Past {index}', f'extra-past-{index}',
                start_date=today - datetime.timedelta(days=70 + index * 7),
                status='completed',
            )
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded_response = self.client.get('/sprints')

        self.assertEqual(expanded_response.status_code, 200)
        self.assertEqual(
            len(self._membership_queries(expanded_queries)),
            2,
            self._membership_queries(expanded_queries),
        )

    def test_anonymous_viewer_issues_no_membership_queries(self):
        _create_sprint('Anonymous Current', 'anonymous-current')

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/sprints')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._membership_queries(queries), [])
        self.assertContains(response, 'href="/sprints/anonymous-current"')
        self.assertNotContains(response, 'Log in to join')

    def test_authenticated_empty_index_issues_no_membership_queries(self):
        member = User.objects.create_user(
            email='empty-sprints@example.com', password='pw',
        )
        self.client.force_login(member)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/sprints')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._membership_queries(queries), [])
        self.assertContains(response, 'data-testid="sprints-empty"')


class SprintCardNavigationTest(TestCase):
    """Sprint index rows always navigate to detail, regardless of viewer state."""

    @staticmethod
    def _past_start(duration_weeks=4, days_after_end=14):
        """Start date for a sprint whose window ended ``days_after_end`` ago."""
        return (
            timezone.localdate()
            - datetime.timedelta(weeks=duration_weeks)
            - datetime.timedelta(days=days_after_end)
        )

    def _detail_href(self, slug):
        return (
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": slug})}"'
        )

    def test_ended_sprint_anonymous_links_to_detail_without_cta(self):
        sprint = _create_sprint(
            'Ended Anon Sprint',
            'ended-anon-sprint',
            start_date=self._past_start(),
            status='completed',
            min_tier_level=20,
        )

        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn(sprint.name, card)
        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Log in to join', card)
        self.assertNotIn('View sprint', card)
        self.assertNotIn('sprints-sprint-cta', card)
        self.assertNotIn(
            f'href="{reverse("account_login")}?next=/sprints/{sprint.slug}"',
            card,
        )

    def test_ended_sprint_below_tier_member_links_to_detail_without_cta(self):
        sprint = _create_sprint(
            'Ended Premium Sprint',
            'ended-premium-sprint',
            start_date=self._past_start(),
            status='completed',
            min_tier_level=30,
        )
        member = User.objects.create_user(
            email='free1315@example.com', password='pw',
        )
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn(sprint.name, card)
        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Upgrade to', card)
        self.assertNotIn(f'href="{reverse("pricing")}"', card)

    def test_ended_sprint_eligible_member_links_to_detail_without_cta(self):
        sprint = _create_sprint(
            'Ended Eligible Sprint',
            'ended-eligible-sprint',
            start_date=self._past_start(),
            status='completed',
            min_tier_level=20,
        )
        member = User.objects.create_user(
            email='main1315@example.com', password='pw',
        )
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn(sprint.name, card)
        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Log in to join', card)
        self.assertNotIn('Upgrade to', card)

    def test_ended_sprint_enrolled_with_plan_links_to_detail(self):
        sprint = _create_sprint(
            'Ended Enrolled Plan Sprint',
            'ended-enrolled-plan-sprint',
            start_date=self._past_start(),
            status='completed',
            min_tier_level=20,
        )
        member = User.objects.create_user(
            email='plan1315@example.com', password='pw',
        )
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = Plan.objects.create(
            member=member, sprint=sprint, visibility='cohort',
        )

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn("You're enrolled", card)
        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Open my plan', card)
        self.assertNotIn('View sprint', card)
        self.assertNotIn(
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
            card,
        )

    def test_ended_sprint_enrolled_without_plan_links_to_detail(self):
        sprint = _create_sprint(
            'Ended Enrolled Board Sprint',
            'ended-enrolled-board-sprint',
            start_date=self._past_start(),
            status='completed',
            min_tier_level=20,
        )
        member = User.objects.create_user(
            email='board1315@example.com', password='pw',
        )
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn("You're enrolled", card)
        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Open cohort board', card)
        self.assertNotIn('View sprint', card)
        self.assertNotIn(
            reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
            card,
        )

    def test_active_sprint_anonymous_links_to_detail_without_cta(self):
        sprint = _create_sprint(
            'Active Join Sprint',
            'active-join-sprint',
            start_date=_active_sprint_start(),
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Log in to join', card)
        self.assertNotIn(
            f'href="{reverse("account_login")}?next=/sprints/{sprint.slug}"',
            card,
        )
        self.assertNotIn('View sprint', card)

    def test_active_sprint_below_tier_member_links_to_detail_without_cta(self):
        sprint = _create_sprint(
            'Active Premium Sprint',
            'active-premium-sprint',
            start_date=_active_sprint_start(),
            status='active',
            min_tier_level=30,
        )
        member = User.objects.create_user(
            email='freeactive1315@example.com', password='pw',
        )
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/sprints')
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn('Premium', card)
        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Upgrade to Premium', card)
        self.assertNotIn(f'href="{reverse("pricing")}"', card)
        self.assertNotIn('View sprint', card)

    def test_exact_end_date_boundary_links_to_detail_without_cta(self):
        # end_date == today: still grouped in the Current section, but
        # has_ended() is already True and joins are already rejected.
        today = timezone.localdate()
        sprint = _create_sprint(
            'Ending Today Sprint',
            'ending-today-sprint',
            start_date=today - datetime.timedelta(weeks=4),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )
        self.assertTrue(sprint.has_ended(today))

        response = self.client.get('/sprints')
        # The sprint stays in the Current section on its exact end date.
        self.assertIn(sprint.name, _section_markup(response, 'current'))
        card = _sprint_card_markup(response, sprint.slug)

        self.assertIn(self._detail_href(sprint.slug), card)
        self.assertNotIn('Log in to join', card)
        self.assertNotIn('View sprint', card)

    def test_past_section_has_no_join_or_upgrade_anchor(self):
        for index, tier_level in enumerate((0, 20, 30)):
            _create_sprint(
                f'Past Tier {tier_level}',
                f'past-tier-{index}',
                start_date=self._past_start(days_after_end=14 + index * 7),
                status='completed',
                min_tier_level=tier_level,
            )

        response = self.client.get('/sprints')
        past = _section_markup(response, 'past')

        self.assertIn('data-testid="sprints-section-past"', past)
        self.assertNotIn('Log in to join', past)
        self.assertNotIn('Upgrade to', past)
        self.assertNotIn('>Upgrade to', past)
        self.assertNotIn('View sprint', past)
        self.assertEqual(past.count('data-testid="sprints-sprint-link"'), 3)
