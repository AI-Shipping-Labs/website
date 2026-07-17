"""Regression coverage for Studio irreversible-action guards (#1282)."""

import datetime
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone

from events.models import Event, EventRegistration
from notifications.services.notification_service import (
    get_series_notification_eligible_user_count,
)
from plans.models import NextStep, Plan, Sprint
from studio.views.notifications import notification_action_context

User = get_user_model()


@tag('core')
class NotificationConfirmationCopyTest(SimpleTestCase):
    def test_content_channel_copy_and_zero_count_remain_actionable(self):
        content = Mock()
        with patch(
            'studio.views.notifications.get_notification_eligible_user_count',
            return_value=0,
        ):
            context = notification_action_context('article', content)

        self.assertEqual(context['notify_button_label'], 'Notify 0 eligible members')
        self.assertEqual(
            context['notify_confirmation'],
            'Notify 0 eligible members in app and post to #announcements? '
            'This cannot be undone.',
        )
        self.assertEqual(
            context['slack_confirmation'],
            'Post this announcement to the configured #announcements '
            'channel? This cannot be undone.',
        )

    def test_event_and_workshop_copy_name_the_actual_channels(self):
        content = Mock()
        with patch(
            'studio.views.notifications.get_notification_eligible_user_count',
            return_value=7,
        ):
            event_context = notification_action_context(
                'event', content, includes_slack=False,
            )
        self.assertEqual(
            event_context['notify_confirmation'],
            'Notify 7 eligible members in app? This cannot be undone.',
        )

        with (
            patch(
                'studio.views.notifications.get_notification_eligible_user_count',
                return_value=7,
            ),
            patch(
                'studio.views.notifications.get_email_eligible_users',
            ) as eligible_email,
        ):
            eligible_email.return_value.count.return_value = 3
            workshop_context = notification_action_context('workshop', content)
        self.assertEqual(
            workshop_context['notify_confirmation'],
            'Notify 7 eligible members in app, email 3 eligible workshop '
            'subscribers, and post to #announcements? This cannot be undone.',
        )

    def test_series_count_uses_lowest_upcoming_session_level(self):
        sessions = [Mock(required_level=30), Mock(required_level=10)]
        queryset = Mock()
        queryset.count.return_value = 4
        with (
            patch(
                'notifications.services.slack_announcements.'
                '_series_upcoming_sessions',
                return_value=sessions,
            ),
            patch(
                'notifications.services.notification_service.'
                '_get_eligible_users',
                return_value=queryset,
            ) as eligible,
        ):
            self.assertEqual(
                get_series_notification_eligible_user_count(Mock()), 4,
            )
        eligible.assert_called_once_with(10)


@tag('core')
class EventScheduleConfirmationContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='guard-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='guard-member@test.com', password='pw',
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='pw')

    def test_local_edit_exposes_persisted_schedule_and_prefiltered_count(self):
        start = timezone.now() + datetime.timedelta(days=5)
        event = Event.objects.create(
            title='Guarded event', slug='guarded-event',
            start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=2),
            timezone='UTC', origin='studio',
        )
        EventRegistration.objects.create(event=event, user=self.member)

        response = self.client.get(f'/studio/events/{event.pk}/edit')

        self.assertContains(response, 'data-reschedule-guard="true"')
        self.assertContains(response, 'data-reschedule-registration-count="1"')
        self.assertContains(
            response,
            f'data-reschedule-initial-start-ms="{int(start.timestamp() * 1000)}"',
        )
        self.assertContains(
            response,
            "'Saving will email ' + attendeeLabel",
        )

    def test_create_and_synced_edit_do_not_attach_schedule_guard(self):
        create_response = self.client.get('/studio/events/new')
        self.assertNotContains(create_response, 'data-reschedule-guard="true"')

        synced = Event.objects.create(
            title='Synced', slug='synced-guard',
            start_datetime=timezone.now() + datetime.timedelta(days=5),
            origin='github', source_repo='owner/repo',
        )
        edit_response = self.client.get(f'/studio/events/{synced.pk}/edit')
        self.assertNotContains(edit_response, 'data-reschedule-guard="true"')


@tag('core')
class PlanConfirmationContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='plan-guard-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='plan-guard-member@test.com', password='pw',
        )
        cls.target_sprint = Sprint.objects.create(
            name='August Build', slug='august-build-1282',
            start_date=datetime.date(2026, 8, 1),
        )
        cls.target = Plan.objects.create(
            member=cls.member, sprint=cls.target_sprint,
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='pw')

    def test_no_source_copy_is_form_owned_and_names_target(self):
        response = self.client.get(f'/studio/plans/{self.target.pk}/')
        carry = (
            'No prior sprint plan is available to carry into August Build. '
            'Continue?'
        )
        draft = (
            'Draft the August Build sprint plan with the LLM? No prior plan '
            'is available for carry-over. The draft is held for review, not '
            'published. Continue?'
        )
        self.assertContains(response, carry)
        self.assertContains(response, draft)
        self.assertContains(response, 'onsubmit="return confirm(')

    def test_existing_source_copy_uses_exact_unfinished_count(self):
        source_sprint = Sprint.objects.create(
            name='July Build', slug='july-build-1282',
            start_date=datetime.date(2026, 7, 1),
        )
        source = Plan.objects.create(member=self.member, sprint=source_sprint)
        NextStep.objects.create(plan=source, description='Ship the demo')

        response = self.client.get(f'/studio/plans/{self.target.pk}/')

        self.assertContains(
            response,
            'Carry 1 unfinished task from July Build into August Build? '
            'This cannot be undone.',
        )
        self.assertContains(
            response,
            'Draft the August Build sprint plan with the LLM and carry 1 '
            'unfinished task from July Build? The draft is held for review, '
            'not published. Continue?',
        )
