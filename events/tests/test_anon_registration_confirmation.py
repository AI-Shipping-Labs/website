"""Anonymous event-registration confirmation is session-bound (issue #1508).

Query-string ``registered`` / ``account_created`` values must not render
the success block. Display email comes from the registration/user row.
"""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from content.access import LEVEL_MAIN, LEVEL_OPEN
from events.models import Event, EventRegistration
from events.services.anon_registration_confirmation import SESSION_KEY
from events.services.cancel_token import generate_cancel_token
from tests.fixtures import TierSetupMixin


def _post_anon_register(client, slug, email, **extra):
    return client.post(
        f'/api/events/{slug}/register',
        data=json.dumps({'email': email}),
        content_type='application/json',
        **extra,
    )


class AnonRegistrationConfirmationTest(TierSetupMixin, TestCase):
    """Session flash + clean-URL confirmation after anonymous register."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Open Community Call',
            slug='open-call-confirm',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_successful_register_shows_confirmation_on_clean_url(
        self, mock_reg_email, mock_verify,
    ):
        email = 'new-anon@test.com'
        resp = _post_anon_register(self.client, self.event.slug, email)
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['status'], 'registered')
        self.assertEqual(body['event_slug'], self.event.slug)
        self.assertTrue(body['account_created'])
        self.assertIn('registered_at', body)
        self.assertNotIn('email', body)
        self.assertNotIn('redirect_url', body)

        detail = self.client.get(self.event.get_absolute_url())
        self.assertEqual(detail.context['anon_registered_email'], email)
        self.assertTrue(detail.context['anon_registered_account_created'])
        self.assertContains(detail, 'event-anonymous-registered-confirmation')
        self.assertContains(
            detail,
            'data-testid="event-anonymous-registered-email"',
        )
        self.assertContains(detail, email)
        self.assertContains(detail, "You're registered for Open Community Call")
        self.assertContains(detail, 'calendar invite')
        self.assertContains(detail, 'verification link')
        self.assertContains(detail, 'event-anonymous-add-to-calendar')
        self.assertContains(
            detail, f'/events/{self.event.slug}/calendar.ics',
        )
        self.assertContains(detail, 'event-anonymous-manage-link')
        self.assertContains(detail, 'Sign in to manage your registration')
        self.assertContains(
            detail,
            f'/accounts/login/?next={self.event.get_absolute_url()}',
        )
        self.assertNotContains(detail, 'event-anonymous-email-form')

        refresh = self.client.get(self.event.get_absolute_url())
        self.assertEqual(refresh.context['anon_registered_email'], email)
        self.assertContains(refresh, 'event-anonymous-registered-confirmation')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_idempotent_resubmit_lands_on_confirmation_without_duplicate(
        self, mock_reg_email, mock_verify,
    ):
        email = 'repeat-anon@test.com'
        first = _post_anon_register(self.client, self.event.slug, email)
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()['account_created'])

        second = _post_anon_register(
            self.client, self.event.slug, email, REMOTE_ADDR='198.51.100.40',
        )
        self.assertEqual(second.status_code, 201)
        self.assertFalse(second.json()['account_created'])
        self.assertTrue(second.json()['already_registered'])

        user = User.objects.get(email=email)
        self.assertEqual(User.objects.filter(email__iexact=email).count(), 1)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=user,
            ).count(),
            1,
        )

        detail = self.client.get(self.event.get_absolute_url())
        self.assertEqual(detail.context['anon_registered_email'], email)
        self.assertFalse(detail.context['anon_registered_account_created'])
        self.assertContains(detail, 'event-anonymous-registered-confirmation')
        self.assertContains(detail, email)
        self.assertNotContains(detail, 'verification link')

    def test_spoofed_query_does_not_copy_into_session(self):
        canonical = self.event.get_absolute_url()
        victim = 'victim@example.com'
        resp = self.client.get(
            f'{canonical}?registered={victim}&account_created=1',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], canonical)
        self.assertNotIn(SESSION_KEY, self.client.session)

        followed = self.client.get(canonical)
        self.assertEqual(followed.context['anon_registered_email'], '')
        self.assertFalse(followed.context['anon_registered_account_created'])
        self.assertContains(followed, 'event-anonymous-email-form')
        self.assertNotContains(
            followed, 'event-anonymous-registered-confirmation',
        )
        self.assertNotContains(followed, victim)
        self.assertNotContains(followed, 'event-anonymous-add-to-calendar')
        self.assertNotContains(followed, 'event-anonymous-manage-link')

    def test_account_created_only_query_redirects_to_form(self):
        canonical = self.event.get_absolute_url()
        resp = self.client.get(f'{canonical}?account_created=1')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], canonical)

        followed = self.client.get(canonical)
        self.assertContains(followed, 'event-anonymous-email-form')
        self.assertNotContains(
            followed, 'event-anonymous-registered-confirmation',
        )
        self.assertFalse(followed.context['anon_registered_account_created'])

    def test_spoofed_query_keeps_unrelated_params(self):
        canonical = self.event.get_absolute_url()
        resp = self.client.get(
            f'{canonical}?utm_source=newsletter&registered=victim@example.com',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], f'{canonical}?utm_source=newsletter')
        self.assertNotIn('registered', resp['Location'])

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_confirmation_is_not_shown_for_a_different_event(
        self, mock_reg_email, mock_verify,
    ):
        other = Event.objects.create(
            title='Other Open Call',
            slug='other-open-call-confirm',
            start_datetime=timezone.now() + timedelta(days=8),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )
        email = 'same-browser@test.com'
        resp = _post_anon_register(self.client, self.event.slug, email)
        self.assertEqual(resp.status_code, 201)

        other_page = self.client.get(other.get_absolute_url())
        self.assertEqual(other_page.context['anon_registered_email'], '')
        self.assertContains(other_page, 'event-anonymous-email-form')
        self.assertNotContains(
            other_page, 'event-anonymous-registered-confirmation',
        )
        self.assertNotContains(other_page, email)

        original = self.client.get(self.event.get_absolute_url())
        self.assertEqual(original.context['anon_registered_email'], email)
        self.assertContains(original, 'event-anonymous-registered-confirmation')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_confirmation_not_shown_for_external_past_or_gated(
        self, mock_reg_email, mock_verify,
    ):
        email = 'scope-anon@test.com'
        resp = _post_anon_register(self.client, self.event.slug, email)
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email=email)

        external = Event.objects.create(
            title='External Cohort',
            slug='external-confirm',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/llm-eng',
        )
        EventRegistration.objects.create(event=external, user=user)
        session = self.client.session
        session[SESSION_KEY] = {
            'event_id': external.pk,
            'user_id': user.pk,
            'account_created': True,
        }
        session.save()
        external_page = self.client.get(external.get_absolute_url())
        self.assertEqual(external_page.context['anon_registered_email'], '')
        self.assertContains(external_page, 'event-external-join-card')
        self.assertNotContains(
            external_page, 'event-anonymous-registered-confirmation',
        )
        self.assertNotContains(external_page, 'event-anonymous-email-form')

        past = Event.objects.create(
            title='Past Call',
            slug='past-confirm',
            start_datetime=timezone.now() - timedelta(days=2),
            end_datetime=timezone.now() - timedelta(days=2) + timedelta(hours=1),
            status='completed',
            required_level=LEVEL_OPEN,
        )
        EventRegistration.objects.create(event=past, user=user)
        session = self.client.session
        session[SESSION_KEY] = {
            'event_id': past.pk,
            'user_id': user.pk,
            'account_created': True,
        }
        session.save()
        past_page = self.client.get(past.get_absolute_url())
        self.assertEqual(past_page.context['anon_registered_email'], '')
        self.assertNotContains(
            past_page, 'event-anonymous-registered-confirmation',
        )
        self.assertNotContains(past_page, 'event-anonymous-email-form')

        gated = Event.objects.create(
            title='Main Workshop',
            slug='gated-confirm',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )
        EventRegistration.objects.create(event=gated, user=user)
        session = self.client.session
        session[SESSION_KEY] = {
            'event_id': gated.pk,
            'user_id': user.pk,
            'account_created': True,
        }
        session.save()
        gated_page = self.client.get(gated.get_absolute_url())
        self.assertEqual(gated_page.context['anon_registered_email'], '')
        self.assertContains(gated_page, 'event-anonymous-cta')
        self.assertNotContains(
            gated_page, 'event-anonymous-registered-confirmation',
        )
        self.assertNotContains(gated_page, 'event-anonymous-email-form')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_confirmation_hidden_when_registration_row_is_gone(
        self, mock_reg_email, mock_verify,
    ):
        email = 'deleted-row@test.com'
        resp = _post_anon_register(self.client, self.event.slug, email)
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email=email)
        EventRegistration.objects.filter(event=self.event, user=user).delete()

        detail = self.client.get(self.event.get_absolute_url())
        self.assertEqual(detail.context['anon_registered_email'], '')
        self.assertContains(detail, 'event-anonymous-email-form')
        self.assertNotContains(
            detail, 'event-anonymous-registered-confirmation',
        )

    def test_authenticated_register_does_not_set_anon_flash(self):
        user = User.objects.create_user(
            email='auth-confirm@test.com',
            password='realpassword12',
            email_verified=True,
        )
        self.client.force_login(user)
        resp = self.client.post(f'/api/events/{self.event.slug}/register')
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_inbox_cancel_url_has_token_and_no_email(self):
        user = User.objects.create_user(email='cancel-url@test.com')
        registration = EventRegistration.objects.create(
            event=self.event, user=user,
        )
        token = generate_cancel_token(registration)
        cancel_url = (
            f'/events/{self.event.slug}/cancel-registration?token={token}'
        )
        self.assertNotIn('email=', cancel_url)
        self.assertNotIn(user.email, cancel_url)
        self.assertIn('token=', cancel_url)

        page = self.client.get(cancel_url)
        self.assertContains(page, 'Cancel')
        self.assertNotContains(page, 'email=')
        self.assertNotContains(page, f'registered={user.email}')

    def test_registration_card_ctas_use_button_classes(self):
        template_path = (
            Path(__file__).resolve().parent.parent.parent
            / 'templates' / 'events' / '_event_registration_card.html'
        )
        body = template_path.read_text()
        self.assertIn("{% button_classes 'primary' extra='w-full sm:w-auto' %}", body)
        self.assertIn('id="event-anon-submit-btn"', body)
        self.assertIn('event-anonymous-add-to-calendar', body)
        self.assertIn('event-anonymous-manage-link', body)
        confirm_start = body.index('event-anonymous-registered-confirmation')
        confirm_end = body.index('event-anonymous-email-form')
        confirmation = body[confirm_start:confirm_end]
        self.assertEqual(
            confirmation.count("{% button_classes 'secondary' %}"),
            2,
        )
        self.assertNotIn('content_gated.html', body)
        self.assertIn('content/_gated_access_card.html', body)
