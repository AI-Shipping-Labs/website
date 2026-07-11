import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
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


class SprintsIndexTest(TestCase):
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
        self.assertContains(response, 'Joining requires Main membership')
        self.assertContains(response, 'Log in to join')
        self.assertContains(
            response,
            f'href="{reverse("account_login")}?next=/sprints/{sprint.slug}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": sprint.slug})}"',
        )

    def test_member_cta_points_to_pricing_when_under_required_tier(self):
        _create_sprint(
            'Premium Sprint',
            'premium-sprint',
            min_tier_level=30,
        )
        member = User.objects.create_user(email='free545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/sprints')

        self.assertContains(response, 'Upgrade to Premium')
        self.assertContains(response, f'href="{reverse("pricing")}"')

    def test_enrolled_member_cta_points_to_existing_plan(self):
        sprint = _create_sprint('Main Sprint', 'main-sprint')
        member = User.objects.create_user(email='main545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = Plan.objects.create(member=member, sprint=sprint, visibility='cohort')

        self.client.force_login(member)
        response = self.client.get('/sprints')

        self.assertContains(response, 'Open my plan')
        self.assertContains(response, "You're enrolled")
        self.assertNotContains(response, 'Use the next step below to continue')
        self.assertContains(
            response,
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
        )

    def test_enrolled_member_without_plan_cta_points_to_board(self):
        sprint = _create_sprint('Board Sprint', 'board-sprint')
        member = User.objects.create_user(email='board545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)

        self.client.force_login(member)
        response = self.client.get('/sprints')

        self.assertContains(response, 'Open cohort board')
        self.assertContains(response, "You're enrolled")
        self.assertNotContains(response, 'Use the next step below to continue')
        self.assertContains(
            response,
            reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
        )

    def test_sprint_detail_route_still_resolves_after_index_route(self):
        sprint = _create_sprint('Detail Sprint', 'detail-sprint')

        response = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'plans/sprint_detail.html')
        self.assertContains(response, 'data-testid="sprint-detail-name"')
