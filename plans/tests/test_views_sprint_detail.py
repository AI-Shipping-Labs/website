"""Tests for the public sprint detail page (issue #443).

The detail page renders one of four CTAs based on viewer state and is
the entry point for self-join. These tests cover the four CTA branches,
the draft-status hiding rule, and the tier-name rendering.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from events.models import Event, EventSeries
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()
FROZEN_CALL_NOW = '2026-06-15T12:00:00Z'


def _active_sprint_start_date():
    """Keep CTA fixtures inside their participation window."""
    return timezone.localdate() - datetime.timedelta(days=7)


def _premium_user(email):
    """Create a Premium-tier user so eligibility tests pass."""
    user = User.objects.create_user(email=email, password='pw')
    user.tier = Tier.objects.get(slug='premium')
    user.save(update_fields=['tier'])
    return user


def _free_user(email):
    user = User.objects.create_user(email=email, password='pw')
    # New users default to ``free`` already; explicit assignment for clarity.
    user.tier = Tier.objects.get(slug='free')
    user.save(update_fields=['tier'])
    return user


def _main_user(email, *, preferred_timezone=''):
    user = User.objects.create_user(email=email, password='pw')
    user.tier = Tier.objects.get(slug='main')
    user.preferred_timezone = preferred_timezone
    user.save(update_fields=['tier', 'preferred_timezone'])
    return user


def _event_series(slug='sprint-calls'):
    return EventSeries.objects.create(
        name='Sprint calls',
        slug=slug,
        cadence='weekly',
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )


def _series_event(series, *, title, slug, start, end=None, zoom_url=''):
    return Event.objects.create(
        title=title,
        slug=slug,
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=start,
        end_datetime=end,
        timezone='Europe/Berlin',
        status='upcoming',
        origin='studio',
        event_series=series,
        location='Zoom',
        zoom_join_url=zoom_url,
        published=True,
    )


class SprintDetailAnonymousTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=_active_sprint_start_date(),
            status='active', min_tier_level=30,
        )

    def test_anonymous_sees_login_cta(self):
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-login"')
        self.assertContains(response, '/accounts/login/?next=/sprints/may-2026')
        self.assertNotContains(response, 'data-testid="sprint-cta-join"')


class SprintLandingContentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _event_series('landing-calls')
        cls.sprint = Sprint.objects.create(
            name='Build and ship', slug='build-and-ship',
            start_date=_active_sprint_start_date(), duration_weeks=6,
            status='active', min_tier_level=20, event_series=cls.series,
            description='Build a useful AI product.\n\nShip it with your cohort.',
            outcomes='Working prototype\n\nPublic launch',
            audience='First-time AI builders\nExperienced engineers',
        )
        _series_event(
            cls.series,
            title='Kickoff', slug='landing-kickoff',
            start=timezone.now() + datetime.timedelta(days=1),
        )

    def _get(self):
        return self.client.get(reverse(
            'sprint_detail', kwargs={'sprint_slug': self.sprint.slug},
        ))

    def test_authored_landing_sections_render_before_primary_action(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        testids = [
            'sprint-landing-about',
            'sprint-landing-includes',
            'sprint-landing-schedule',
            'sprint-landing-outcomes',
            'sprint-landing-audience',
            'sprint-primary-action',
        ]
        positions = [body.index(f'data-testid="{testid}"') for testid in testids]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, 'Build a useful AI product.')
        self.assertContains(response, 'Ship it with your cohort.')
        self.assertNotContains(response, 'sprint-landing-about-generic')
        self.assertContains(response, 'Weekly sprint calls')
        self.assertContains(response, 'A personal sprint plan')
        self.assertContains(response, 'Accountability partners')
        self.assertContains(response, 'The cohort board')
        self.assertContains(response, '1 call')

    def test_line_fields_render_nonempty_items_and_escape_html(self):
        self.sprint.outcomes = 'Working prototype\n\n<script>alert(1)</script>'
        self.sprint.audience = 'Builders\n\nEngineers'
        self.sprint.description = '<strong>Plain text only</strong>'
        self.sprint.save(update_fields=['description', 'outcomes', 'audience'])

        response = self._get()

        self.assertContains(response, '&lt;strong&gt;Plain text only&lt;/strong&gt;')
        self.assertNotContains(response, '<strong>Plain text only</strong>')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        body = response.content.decode()
        outcomes = body[
            body.index('data-testid="sprint-landing-outcomes"'):
            body.index('</section>', body.index('data-testid="sprint-landing-outcomes"'))
        ]
        self.assertEqual(outcomes.count('<li>'), 2)

    def test_blank_fields_use_generic_about_and_omit_optional_sections(self):
        self.sprint.description = ''
        self.sprint.outcomes = ''
        self.sprint.audience = ''
        self.sprint.event_series = None
        self.sprint.save(update_fields=[
            'description', 'outcomes', 'audience', 'event_series',
        ])

        response = self._get()
        body = response.content.decode()

        self.assertContains(response, 'data-testid="sprint-landing-about-generic"')
        self.assertContains(response, 'data-testid="sprint-landing-includes"')
        self.assertContains(response, 'data-testid="sprint-landing-schedule"')
        self.assertNotContains(response, 'data-testid="sprint-landing-outcomes"')
        self.assertNotContains(response, 'data-testid="sprint-landing-audience"')
        self.assertNotContains(response, 'Scheduled calls')
        self.assertLess(
            body.index('data-testid="sprint-landing-schedule"'),
            body.index('data-testid="sprint-primary-action"'),
        )

    def test_authenticated_non_enrolled_viewers_see_landing_before_their_cta(self):
        viewers = [
            (_free_user('landing-free@test.com'), 'sprint-cta-upgrade'),
            (_main_user('landing-main@test.com'), 'sprint-cta-join'),
        ]
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        for viewer, cta_testid in viewers:
            with self.subTest(viewer=viewer.email):
                self.client.force_login(viewer)
                response = self.client.get(url)
                body = response.content.decode()
                self.assertLess(
                    body.index('data-testid="sprint-landing-about"'),
                    body.index('data-testid="sprint-primary-action"'),
                )
                self.assertContains(response, f'data-testid="{cta_testid}"')
                self.client.logout()

    def test_enrolled_view_keeps_action_first_and_has_no_landing_sections(self):
        member = _main_user('landing-enrolled@test.com')
        SprintEnrollment.objects.create(sprint=self.sprint, user=member)
        self.client.force_login(member)

        response = self._get()
        body = response.content.decode()

        self.assertContains(response, 'data-testid="sprint-cta-enrolled"')
        self.assertNotContains(response, 'data-testid="sprint-landing"')
        self.assertLess(
            body.index('data-testid="sprint-primary-action"'),
            body.index('data-testid="sprint-meeting-schedule"'),
        )

    def test_cancelled_and_ended_non_enrolled_views_keep_landing_before_action(self):
        states = [
            ('cancelled', _active_sprint_start_date(), 'sprint-cta-cancelled'),
            (
                'active',
                timezone.localdate() - datetime.timedelta(weeks=8),
                'sprint-cta-ended',
            ),
        ]
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        for status, start_date, cta_testid in states:
            with self.subTest(status=status, cta_testid=cta_testid):
                self.sprint.status = status
                self.sprint.start_date = start_date
                self.sprint.save(update_fields=['status', 'start_date'])
                response = self.client.get(url)
                body = response.content.decode()
                self.assertContains(response, f'data-testid="{cta_testid}"')
                self.assertLess(
                    body.index('data-testid="sprint-landing-schedule"'),
                    body.index('data-testid="sprint-primary-action"'),
                )


class SprintDetailDraftHidingTest(TestCase):
    """Draft sprints are hidden from anonymous and non-staff users."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Draft', slug='draft',
            start_date=datetime.date(2026, 5, 1),
            status='draft',
        )

    def test_draft_returns_404_for_anonymous(self):
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_draft_returns_404_for_member(self):
        member = User.objects.create_user(email='m@test.com', password='pw')
        self.client.force_login(member)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_draft_renders_for_staff(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class SprintDetailUnderTierTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Premium-only', slug='premium-only',
            start_date=_active_sprint_start_date(),
            status='active', min_tier_level=30,
        )
        cls.free_user = _free_user('free@test.com')

    def test_under_tier_user_sees_upgrade_cta(self):
        self.client.force_login(self.free_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-upgrade"')
        self.assertContains(response, 'Upgrade to Premium to join')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, 'data-testid="sprint-cta-join"')


class SprintDetailEligibleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Premium-only', slug='premium-only',
            start_date=_active_sprint_start_date(),
            status='active', min_tier_level=30,
        )
        cls.premium_user = _premium_user('p@test.com')

    def test_eligible_not_enrolled_sees_join_button(self):
        self.client.force_login(self.premium_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-join"')
        self.assertNotContains(response, 'data-testid="sprint-cta-upgrade"')
        self.assertNotContains(response, 'data-testid="sprint-cta-enrolled"')

    def test_enrolled_sees_leave_button_and_board_link(self):
        SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.premium_user,
        )
        self.client.force_login(self.premium_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-cta-enrolled"')
        self.assertContains(response, 'data-testid="sprint-cta-leave"')
        self.assertContains(response, 'data-testid="sprint-cta-board"')


class SprintDetailCommentLeakTest(TestCase):
    """Issue #807: the #598 developer note must never reach rendered HTML.

    The note sits inside the join-CTA branch, so an eligible,
    not-yet-enrolled member is used to force it to render. The asserted
    phrases are substrings of the comment body only -- they appear
    nowhere in legitimate visible copy. These assertions FAIL against
    the old multi-line ``{# #}`` template and PASS once it is a
    ``{% comment %}`` block.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Premium-only', slug='premium-only',
            start_date=_active_sprint_start_date(),
            status='active', min_tier_level=30,
        )
        cls.premium_user = _premium_user('p@test.com')

    def test_detail_does_not_leak_598_developer_note(self):
        self.client.force_login(self.premium_user)
        url = reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Confirm the join CTA (which wraps the #598 note) rendered,
        # otherwise the leak guard would be vacuous.
        self.assertContains(response, 'data-testid="sprint-cta-join"')
        self.assertNotContains(response, 'emerald color override')
        self.assertNotContains(response, 'win the cascade')


class SprintDetailEndedParticipationTest(TestCase):
    """Ended self-join closes without hiding existing enrolled work (#1233)."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.sprint = Sprint.objects.create(
            name='Ended cohort', slug='ended-cohort',
            start_date=cls.today - datetime.timedelta(weeks=6),
            duration_weeks=6, status='active', min_tier_level=30,
        )
        cls.free_user = _free_user('ended-free@test.com')
        cls.premium_user = _premium_user('ended-premium@test.com')

    def _assert_ended_closure(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['sprint_has_ended'])
        self.assertContains(response, 'data-testid="sprint-cta-ended"')
        self.assertContains(
            response,
            'This sprint has ended and is no longer open to join.',
        )
        for hidden_testid in (
            'sprint-cta-login', 'sprint-cta-upgrade', 'sprint-cta-join',
        ):
            self.assertNotContains(
                response, f'data-testid="{hidden_testid}"',
            )

    def test_anonymous_sees_ended_closure_instead_of_login(self):
        response = self.client.get(self.sprint.get_absolute_url())
        self._assert_ended_closure(response)

    def test_under_tier_member_sees_ended_closure_instead_of_upgrade(self):
        self.client.force_login(self.free_user)
        response = self.client.get(self.sprint.get_absolute_url())
        self._assert_ended_closure(response)

    def test_eligible_member_sees_ended_closure_instead_of_join(self):
        self.client.force_login(self.premium_user)
        response = self.client.get(self.sprint.get_absolute_url())
        self._assert_ended_closure(response)

    def test_enrolled_member_keeps_plan_action_after_sprint_ends(self):
        plan = Plan.objects.create(
            sprint=self.sprint, member=self.premium_user,
        )
        self.client.force_login(self.premium_user)

        response = self.client.get(self.sprint.get_absolute_url())

        self.assertContains(response, 'data-testid="sprint-cta-enrolled"')
        self.assertContains(response, 'data-testid="sprint-cta-open-plan"')
        self.assertContains(
            response,
            reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': self.sprint.slug,
                    'plan_id': plan.pk,
                },
            ),
        )
        self.assertNotContains(response, 'data-testid="sprint-cta-ended"')
        self.assertNotContains(response, 'data-testid="sprint-cta-join"')

    def test_cancelled_ended_sprint_keeps_cancelled_explanation(self):
        self.sprint.status = 'cancelled'
        self.sprint.save(update_fields=['status'])

        response = self.client.get(self.sprint.get_absolute_url())

        self.assertContains(response, 'data-testid="sprint-cta-cancelled"')
        self.assertContains(
            response,
            'This sprint has been cancelled and is no longer open to join.',
        )
        self.assertNotContains(response, 'data-testid="sprint-cta-ended"')

    def test_end_date_boundary_closes_today_but_not_tomorrow(self):
        current = Sprint.objects.create(
            name='Current until tomorrow', slug='current-until-tomorrow',
            start_date=self.today - datetime.timedelta(days=6),
            duration_weeks=1, status='active', min_tier_level=30,
        )
        self.client.force_login(self.premium_user)

        current_response = self.client.get(current.get_absolute_url())
        ended_response = self.client.get(self.sprint.get_absolute_url())

        self.assertEqual(current.end_date, self.today + datetime.timedelta(days=1))
        self.assertContains(current_response, 'data-testid="sprint-cta-join"')
        self.assertEqual(self.sprint.end_date, self.today)
        self.assertContains(ended_response, 'data-testid="sprint-cta-ended"')


class SprintDetailTierBadgeTest(TestCase):
    """The detail badge matches the public tier vocabulary on the index."""

    def test_main_tier_badge_when_min_is_20(self):
        sprint = Sprint.objects.create(
            name='Main+', slug='main-only',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=20,
        )
        url = reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, 'data-testid="sprint-tier-badge"')
        self.assertContains(response, 'data-component="member-badge"')
        self.assertContains(response, 'data-required-level="20"')
        self.assertContains(response, 'data-lucide="lock"')
        self.assertContains(response, 'Main or above')
        self.assertNotContains(response, 'Main tier required')


class SprintDetailDateRangeTest(TestCase):
    """The detail page renders the start--end (duration) range (#978)."""

    def test_same_year_range_with_duration(self):
        sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 17),
            duration_weeks=6, status='active',
        )
        url = reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, 'June 17 – July 29, 2026 (6 weeks)')
        # The old "Starts <date> · N weeks" wording is gone.
        self.assertNotContains(response, 'Starts June 17, 2026')
        # The date-derived badge (#979) is untouched by the date-range
        # feature -- its pill element is still rendered. (Its label is
        # date-derived, not the stored status, and is covered by the
        # sprint_badge model tests, so we do not assert the label here.)
        self.assertContains(response, 'data-testid="sprint-status-badge"')

    def test_cross_year_range_shows_both_years(self):
        sprint = Sprint.objects.create(
            name='Dec 2025', slug='dec-2025',
            start_date=datetime.date(2025, 12, 16),
            duration_weeks=6, status='active',
        )
        url = reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertContains(
            response, 'December 16, 2025 – January 27, 2026 (6 weeks)',
        )

    def test_singular_week_pluralization(self):
        sprint = Sprint.objects.create(
            name='One week', slug='one-week',
            start_date=datetime.date(2026, 6, 17),
            duration_weeks=1, status='active',
        )
        url = reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertContains(response, '(1 week)')
        self.assertNotContains(response, '(1 weeks)')


class SprintDetailCallsTest(TestCase):
    """Sprint detail call rows for issue #981."""

    def setUp(self):
        self.user = _main_user(
            'main-calls@test.com',
            preferred_timezone='America/New_York',
        )
        self.series = _event_series()
        self.sprint = Sprint.objects.create(
            name='June 2026',
            slug='june-2026',
            start_date=datetime.date(2026, 6, 17),
            duration_weeks=6,
            status='active',
            min_tier_level=20,
            event_series=self.series,
        )
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.user)

    def _get(self):
        self.client.force_login(self.user)
        return self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug}),
        )

    def test_call_entry_uses_event_time_display_for_viewer_timezone(self):
        start = datetime.datetime(
            2026, 6, 17, 18, 0, tzinfo=datetime.timezone.utc,
        )
        _series_event(
            self.series,
            title='Kickoff call',
            slug='kickoff-call',
            start=start,
            end=start + datetime.timedelta(hours=1),
        )

        response = self._get()

        self.assertContains(response, 'data-testid="sprint-call-entry"')
        self.assertContains(response, 'Kickoff call')
        self.assertContains(response, 'data-testid="event-time-row"')
        self.assertContains(response, 'data-start-utc="2026-06-17T18:00:00Z"')
        self.assertContains(response, 'data-default-timezone="America/New_York"')
        self.assertContains(response, 'data-browser-timezone-enabled="false"')
        self.assertContains(response, 'Zoom')

    @freeze_time(FROZEN_CALL_NOW)
    def test_upcoming_call_open_now_uses_tracked_join_url(self):
        start = timezone.now() + datetime.timedelta(minutes=4)
        event = _series_event(
            self.series,
            title='Live call',
            slug='live-call',
            start=start,
            end=start + datetime.timedelta(hours=1),
            zoom_url='https://zoom.example.com/raw-live',
        )

        response = self._get()

        self.assertContains(response, 'data-testid="sprint-call-join"')
        # Issue #1082: id-canonical /events/<id>/<slug>/join URL.
        self.assertContains(
            response,
            f'href="{event.get_join_url()}"',
        )
        self.assertContains(response, 'target="_blank"')
        self.assertNotContains(response, 'https://zoom.example.com/raw-live')

    @freeze_time(FROZEN_CALL_NOW)
    def test_upcoming_call_not_open_yet_has_non_link_affordance(self):
        event = _series_event(
            self.series,
            title='Planning call',
            slug='planning-call',
            start=timezone.now() + datetime.timedelta(days=1),
            zoom_url='https://zoom.example.com/raw-planning',
        )

        response = self._get()

        self.assertContains(response, 'data-testid="sprint-call-join-not-open"')
        self.assertContains(response, 'Join link opens ~5 min before start')
        self.assertContains(response, event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="sprint-call-join"')
        self.assertNotContains(response, 'https://zoom.example.com/raw-planning')

    @freeze_time(FROZEN_CALL_NOW)
    def test_past_call_is_marked_past_and_has_no_join_button(self):
        start = timezone.now() - datetime.timedelta(hours=2)
        event = _series_event(
            self.series,
            title='Past call',
            slug='past-call',
            start=start,
            end=start + datetime.timedelta(hours=1),
            zoom_url='https://zoom.example.com/raw-past',
        )

        response = self._get()

        self.assertContains(response, 'Past call')
        self.assertContains(response, 'data-testid="sprint-call-status"')
        self.assertContains(response, 'Past')
        self.assertContains(response, event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="sprint-call-join"')
        self.assertNotContains(response, 'https://zoom.example.com/raw-past')

    def test_no_calls_empty_state_renders_with_action_panel(self):
        response = self._get()

        self.assertContains(response, 'data-testid="sprint-primary-action"')
        self.assertContains(response, 'data-testid="sprint-cta-enrolled"')
        self.assertContains(response, 'data-testid="sprint-calls-empty"')
        self.assertContains(response, 'No calls scheduled yet')
