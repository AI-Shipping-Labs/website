"""Tests for the "Ask the team to plan with me" view (issue #585).

The view body lives in :mod:`plans.views.sprints`. The endpoint is
``POST /sprints/<slug>/ask-team`` and is rate-limited to one ping per
``(sprint, member)`` per 24 hours via the new ``PlanRequest`` audit
table.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification
from plans.models import Plan, PlanRequest, Sprint, SprintEnrollment

User = get_user_model()


def _make_sprint():
    return Sprint.objects.create(
        name='May 2026', slug='may-2026',
        start_date=datetime.date(2026, 5, 1),
        status='active', min_tier_level=0,
    )


def _make_member(email='alex@test.com'):
    return User.objects.create_user(
        email=email, password='pw',
        first_name='Alex', last_name='Member',
    )


def _make_staff(email='staff@test.com'):
    return User.objects.create_user(
        email=email, password='pw', is_staff=True,
    )


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class AskTeamAccessControlTest(TestCase):
    """The endpoint rejects anonymous, non-enrolled, and plan-owning users."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = _make_sprint()

    def test_anonymous_redirects_to_login_no_side_effects(self):
        url = reverse(
            'sprint_ask_team', kwargs={'sprint_slug': self.sprint.slug},
        )
        notif_before = Notification.objects.count()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertEqual(PlanRequest.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), notif_before)
        self.assertEqual(len(mail.outbox), 0)

    def test_non_enrolled_user_gets_404(self):
        member = _make_member()
        # No SprintEnrollment row.
        self.client.force_login(member)
        url = reverse(
            'sprint_ask_team', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PlanRequest.objects.count(), 0)

    def test_member_with_existing_plan_gets_404(self):
        """A member who already owns a plan in this sprint gets 404.

        The button does not render in the UI for plan owners; this
        check enforces it on the server too.
        """
        member = _make_member()
        Plan.objects.create(member=member, sprint=self.sprint)
        # Plan creation back-creates the enrollment via signal, so the
        # member is enrolled. The view should still 404 because they
        # have a plan.
        self.client.force_login(member)
        url = reverse(
            'sprint_ask_team', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PlanRequest.objects.count(), 0)

    def test_get_method_not_allowed(self):
        """The endpoint is POST-only; GET returns 405."""
        member = _make_member()
        SprintEnrollment.objects.create(sprint=self.sprint, user=member)
        self.client.force_login(member)
        url = reverse(
            'sprint_ask_team', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class AskTeamHappyPathTest(TestCase):
    """First successful ping creates audit, notifications, and email."""

    def setUp(self):
        self.sprint = _make_sprint()
        self.member = _make_member()
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.member)
        self.staff_one = _make_staff('staff1@test.com')
        self.staff_two = _make_staff('staff2@test.com')
        # Inactive staff -- must be excluded from fanout.
        self.staff_inactive = User.objects.create_user(
            email='inactive@test.com', password='pw',
            is_staff=True, is_active=False,
        )
        self.client.force_login(self.member)
        self.url = reverse(
            'sprint_ask_team', kwargs={'sprint_slug': self.sprint.slug},
        )

    def test_first_ping_creates_plan_request_and_redirects(self):
        response = self.client.post(self.url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug}),
        )
        self.assertEqual(
            PlanRequest.objects.filter(
                sprint=self.sprint, member=self.member,
            ).count(),
            1,
        )

    def test_first_ping_creates_one_notification_per_active_staff(self):
        self.client.post(self.url)
        plan_request_notifs = Notification.objects.filter(
            notification_type='plan_request',
        )
        # Two active staff, one inactive -> two notifications.
        self.assertEqual(plan_request_notifs.count(), 2)
        recipients = set(
            plan_request_notifs.values_list('user__email', flat=True),
        )
        self.assertEqual(
            recipients,
            {'staff1@test.com', 'staff2@test.com'},
        )

    def test_first_ping_notification_links_to_admin(self):
        self.client.post(self.url)
        notif = Notification.objects.filter(
            notification_type='plan_request',
        ).first()
        self.assertIsNotNone(notif)
        # Admin URL points to the requesting member's accounts/user
        # change page (NOT the plan, which doesn't exist yet).
        self.assertIn(
            f'/admin/accounts/user/{self.member.pk}/change/',
            notif.url,
        )
        # Title carries the member's display name.
        self.assertIn('Alex Member', notif.title)

    def test_first_ping_emails_staff_when_slack_disabled(self):
        # By default in tests SLACK_ENABLED is false, so the email
        # fallback fires.
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(
            set(sent.to), {'staff1@test.com', 'staff2@test.com'},
        )
        self.assertIn('Plan request', sent.subject)
        self.assertIn(self.member.email, sent.subject)
        self.assertIn(self.sprint.name, sent.subject)

    def test_first_ping_shows_success_message(self):
        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any("Asked the team" in m for m in msgs),
            f'Expected success message in {msgs!r}',
        )

    @override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN='xoxb-fake')
    def test_first_ping_posts_to_slack_when_enabled_and_skips_email(self):
        with patch(
            'community.slack_config.get_slack_team_requests_channel_id',
            return_value='C123',
        ):
            with patch('requests.post') as mock_post:
                mock_post.return_value.json.return_value = {'ok': True}
                self.client.post(self.url)
        self.assertEqual(mock_post.call_count, 1)
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(
            kwargs['json']['channel'], 'C123',
        )
        # Email fallback NOT used when Slack succeeded.
        self.assertEqual(len(mail.outbox), 0)
        # Notifications still created for staff.
        self.assertEqual(
            Notification.objects.filter(
                notification_type='plan_request',
            ).count(),
            2,
        )

    @override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN='xoxb-fake')
    def test_slack_no_channel_id_falls_back_to_email(self):
        with patch(
            'community.slack_config.get_slack_team_requests_channel_id',
            return_value='',
        ):
            self.client.post(self.url)
        # Email fallback fires.
        self.assertEqual(len(mail.outbox), 1)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class AskTeamRateLimitTest(TestCase):
    """24h window rate limiting."""

    def setUp(self):
        self.sprint = _make_sprint()
        self.member = _make_member()
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.member)
        _make_staff('s@test.com')
        self.client.force_login(self.member)
        self.url = reverse(
            'sprint_ask_team', kwargs={'sprint_slug': self.sprint.slug},
        )

    def test_repost_within_24h_does_not_create_second_request(self):
        self.client.post(self.url)
        self.assertEqual(PlanRequest.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        notif_count = Notification.objects.filter(
            notification_type='plan_request',
        ).count()

        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.status_code, 200)
        # No new audit row.
        self.assertEqual(PlanRequest.objects.count(), 1)
        # No new email.
        self.assertEqual(len(mail.outbox), 1)
        # No new notifications.
        self.assertEqual(
            Notification.objects.filter(
                notification_type='plan_request',
            ).count(),
            notif_count,
        )
        msgs = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('already pinged' in m.lower() for m in msgs),
            f'Expected "already pinged" message in {msgs!r}',
        )

    def test_repost_after_24h_fires_again(self):
        # Backdate the original ping by 25 hours so the rate-limit
        # window has elapsed.
        old = PlanRequest.objects.create(
            sprint=self.sprint, member=self.member,
        )
        PlanRequest.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=25),
        )
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        # New audit row created.
        self.assertEqual(PlanRequest.objects.count(), 2)
        # Email fanout fires again.
        self.assertEqual(len(mail.outbox), 1)


class AskTeamCohortBoardIntegrationTest(TestCase):
    """The cohort board renders the button and disabled state correctly."""

    def setUp(self):
        self.sprint = _make_sprint()
        self.member = _make_member()
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.member)
        self.client.force_login(self.member)
        self.board_url = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )

    def test_board_renders_ask_team_button_for_no_plan_viewer(self):
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="ask-team-button"')
        self.assertContains(response, 'Ask the team to plan with me')
        # Not pinged recently => the button is NOT in disabled state.
        self.assertFalse(response.context['viewer_pinged_recently'])

    def test_board_disables_button_when_recent_ping_exists(self):
        PlanRequest.objects.create(sprint=self.sprint, member=self.member)
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['viewer_pinged_recently'])
        self.assertContains(
            response, 'Pinged the team', html=False,
        )
        # No POST form rendered when disabled.
        self.assertNotContains(
            response,
            f'action="{reverse("sprint_ask_team", kwargs={"sprint_slug": self.sprint.slug})}"',
        )

    def test_board_does_not_render_button_for_plan_owner(self):
        Plan.objects.create(
            member=self.member, sprint=self.sprint, visibility='cohort',
        )
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="ask-team-button"')

    def test_board_does_not_render_button_on_other_members_no_plan_rows(self):
        """Other members' no_plan rows must not show the ping button."""
        other = User.objects.create_user(
            email='other@test.com', password='pw',
            first_name='Other', last_name='Member',
        )
        SprintEnrollment.objects.create(sprint=self.sprint, user=other)
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        # Both members are visible as no_plan rows.
        self.assertContains(
            response,
            f'data-testid="progress-row-no-plan-{other.pk}"',
        )
        # ... but only one ask-team button: the viewer's.
        content = response.content.decode()
        # Each render of the button carries the testid, count == 1
        # (just the viewer's row in this no-callout-button scenario --
        # the viewer-plan-pending aside callout ALSO renders one).
        self.assertEqual(
            content.count('data-testid="ask-team-button"'),
            2,  # callout + viewer's own no_plan row
        )
