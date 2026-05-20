"""Tests for the EventFeedback model and submission view (issue #679).

Covers:
- Model field defaults, ``unique_together``, and ``clean()`` validation.
- Rating validator rejects values outside ``1..5``.
- ``event_feedback_submit`` POST gating:
    - Anonymous redirects to login.
    - Non-registered user gets 403.
    - Registered user posting before ``end_datetime`` gets 403.
    - Registered user posting after end creates a row and redirects.
    - Second POST updates the same row (no duplicate).
    - Rating-only and comment-only submissions succeed.
    - Fully empty form returns 403.
- Public event_detail context exposes the feedback fields with the
  correct values (aggregate excludes rating-null rows).
- Public template:
    - Upcoming event renders no feedback section at all.
    - Past event with 0 ratings shows no aggregate badge but still
      renders the form for a registered attendee.
    - Past event with >=1 ratings shows badge + form for a registered
      attendee; anonymous sees only the badge.
- Slug-mismatch on the feedback URL 301-redirects to the canonical
  ``/feedback`` URL.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.utils import timezone

from events.models import Event, EventFeedback, EventRegistration

User = get_user_model()


def _make_past_event(**overrides):
    """Return a saved past Event with ``end_datetime`` 1 hour ago."""
    now = timezone.now()
    defaults = {
        'title': 'Past Event',
        'slug': 'past-event',
        'start_datetime': now - timedelta(hours=3),
        'end_datetime': now - timedelta(hours=1),
        'status': 'completed',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _make_upcoming_event(**overrides):
    """Return a saved future Event with ``end_datetime`` 2 hours ahead."""
    now = timezone.now()
    defaults = {
        'title': 'Upcoming Event',
        'slug': 'upcoming-event',
        'start_datetime': now + timedelta(hours=1),
        'end_datetime': now + timedelta(hours=2),
        'status': 'upcoming',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


# --- Model tests ---


@tag('core')
class EventFeedbackModelTest(TestCase):
    """Model defaults, clean() rejection, validator behavior."""

    @classmethod
    def setUpTestData(cls):
        cls.event = _make_past_event()
        cls.user = User.objects.create_user(
            email='attendee@test.com', password='pw',
        )

    def test_create_with_rating_only(self):
        fb = EventFeedback.objects.create(
            event=self.event, user=self.user, rating=4,
        )
        # clean() must accept a rating-only row.
        fb.full_clean()
        self.assertEqual(fb.rating, 4)
        self.assertEqual(fb.comment, '')
        self.assertEqual(fb.would_change, '')

    def test_create_with_comment_only(self):
        fb = EventFeedback.objects.create(
            event=self.event, user=self.user, comment='Loved it',
        )
        fb.full_clean()
        self.assertIsNone(fb.rating)
        self.assertEqual(fb.comment, 'Loved it')

    def test_clean_rejects_all_blank(self):
        """``clean()`` rejects rows where rating, comment, and
        would_change are all empty."""
        fb = EventFeedback(event=self.event, user=self.user)
        with self.assertRaises(ValidationError):
            fb.full_clean()

    def test_clean_rejects_whitespace_only_text(self):
        """Whitespace-only comment + would_change must not satisfy
        the "at least one non-empty" rule."""
        fb = EventFeedback(
            event=self.event, user=self.user,
            comment='   ', would_change='\n\t',
        )
        with self.assertRaises(ValidationError):
            fb.full_clean()

    def test_rating_above_five_rejected(self):
        fb = EventFeedback(event=self.event, user=self.user, rating=6)
        with self.assertRaises(ValidationError):
            fb.full_clean()

    def test_rating_zero_rejected(self):
        fb = EventFeedback(event=self.event, user=self.user, rating=0)
        with self.assertRaises(ValidationError):
            fb.full_clean()


# --- View tests ---


@tag('core')
class EventFeedbackSubmitViewTest(TestCase):
    """Submission-view gating + persistence."""

    @classmethod
    def setUpTestData(cls):
        cls.attendee = User.objects.create_user(
            email='attendee@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )

    def _feedback_url(self, event):
        return f'/events/{event.id}/{event.slug}/feedback'

    def test_anonymous_redirects_to_login(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        response = self.client.post(self._feedback_url(event), {'rating': '4'})
        # @login_required → 302 to /login?next=...
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'].lower())
        self.assertFalse(EventFeedback.objects.exists())

    def test_authenticated_non_attendee_403(self):
        event = _make_past_event()
        # Register a *different* user so the event has registrations
        # but the posting user is not among them.
        EventRegistration.objects.create(event=event, user=self.other)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(self._feedback_url(event), {'rating': '4'})
        self.assertEqual(response.status_code, 403)
        self.assertIn(b'Only registered attendees', response.content)
        self.assertFalse(EventFeedback.objects.exists())

    def test_registered_before_end_403(self):
        event = _make_upcoming_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(self._feedback_url(event), {'rating': '4'})
        self.assertEqual(response.status_code, 403)
        self.assertIn(b'Feedback opens after the event ends', response.content)
        self.assertFalse(EventFeedback.objects.exists())

    def test_registered_without_end_datetime_403(self):
        """An event with a null ``end_datetime`` should also block
        feedback — the spec keys "past" on ``end_datetime <= now``."""
        now = timezone.now()
        event = Event.objects.create(
            title='No End',
            slug='no-end',
            start_datetime=now - timedelta(hours=3),
            end_datetime=None,
            status='completed',
        )
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(self._feedback_url(event), {'rating': '4'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EventFeedback.objects.exists())

    def test_registered_after_end_creates_row(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(
            self._feedback_url(event),
            {'rating': '4', 'comment': 'Great pacing'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'{event.get_absolute_url()}?feedback=thanks',
        )
        fb = EventFeedback.objects.get(event=event, user=self.attendee)
        self.assertEqual(fb.rating, 4)
        self.assertEqual(fb.comment, 'Great pacing')

    def test_second_post_updates_same_row(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        self.client.post(self._feedback_url(event), {'rating': '3'})
        self.client.post(
            self._feedback_url(event),
            {'rating': '5', 'comment': 'Updated'},
        )
        rows = EventFeedback.objects.filter(event=event, user=self.attendee)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows[0].rating, 5)
        self.assertEqual(rows[0].comment, 'Updated')

    def test_rating_only_succeeds(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(self._feedback_url(event), {'rating': '5'})
        self.assertEqual(response.status_code, 302)
        fb = EventFeedback.objects.get(event=event, user=self.attendee)
        self.assertEqual(fb.rating, 5)
        self.assertEqual(fb.comment, '')

    def test_comment_only_succeeds_and_excluded_from_average(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(
            self._feedback_url(event),
            {'comment': 'Sound was choppy'},
        )
        self.assertEqual(response.status_code, 302)
        fb = EventFeedback.objects.get(event=event, user=self.attendee)
        self.assertIsNone(fb.rating)
        self.assertEqual(fb.comment, 'Sound was choppy')
        # Public aggregate counts only rated entries.
        count = event.feedback.filter(rating__isnull=False).count()
        self.assertEqual(count, 0)

    def test_fully_empty_submission_rejected(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(self._feedback_url(event), {})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EventFeedback.objects.exists())

    def test_rating_out_of_range_rejected(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.post(self._feedback_url(event), {'rating': '7'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EventFeedback.objects.exists())

    def test_get_request_not_allowed(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        response = self.client.get(self._feedback_url(event))
        # @require_POST → 405
        self.assertEqual(response.status_code, 405)

    def test_slug_mismatch_redirects_to_canonical(self):
        event = _make_past_event(slug='canonical-slug')
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='attendee@test.com', password='pw')
        wrong_url = f'/events/{event.id}/wrong-slug/feedback'
        response = self.client.post(wrong_url, {'rating': '4'})
        # 301 to the canonical /feedback URL — we don't follow it
        # because Django's test client won't re-POST after a 301.
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'/events/{event.id}/canonical-slug/feedback',
        )


# --- Aggregate / context tests ---


@tag('core')
class EventDetailFeedbackContextTest(TestCase):
    """``event_detail`` view exposes the right feedback context."""

    @classmethod
    def setUpTestData(cls):
        cls.attendee = User.objects.create_user(
            email='att@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )

    def test_upcoming_event_event_is_past_false(self):
        event = _make_upcoming_event()
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['event_is_past'])
        self.assertEqual(response.context['feedback_count'], 0)

    def test_past_event_aggregate_excludes_rating_null(self):
        event = _make_past_event()
        # 3 rated entries: 5, 4, 5 → avg 4.67 → rounded 4.7
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='u1@t.com', password='pw'),
            rating=5,
        )
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='u2@t.com', password='pw'),
            rating=4,
        )
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='u3@t.com', password='pw'),
            rating=5,
        )
        # 1 comment-only entry — must NOT affect avg or count.
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='u4@t.com', password='pw'),
            comment='Nice',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['event_is_past'])
        self.assertEqual(response.context['feedback_count'], 3)
        self.assertEqual(response.context['feedback_avg'], 4.7)

    def test_user_feedback_populated_for_authenticated_attendee(self):
        event = _make_past_event()
        fb = EventFeedback.objects.create(
            event=event, user=self.attendee, rating=4,
        )
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.context['user_feedback'].pk, fb.pk)

    def test_can_submit_feedback_requires_registration_and_past(self):
        # Past + registered → can submit.
        past = _make_past_event(slug='past-1')
        EventRegistration.objects.create(event=past, user=self.attendee)
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(past.get_absolute_url())
        self.assertTrue(response.context['can_submit_feedback'])

        # Past + NOT registered → cannot.
        past2 = _make_past_event(slug='past-2')
        response = self.client.get(past2.get_absolute_url())
        self.assertFalse(response.context['can_submit_feedback'])

        # Upcoming + registered → cannot.
        upcoming = _make_upcoming_event(slug='upc-1')
        EventRegistration.objects.create(event=upcoming, user=self.attendee)
        response = self.client.get(upcoming.get_absolute_url())
        self.assertFalse(response.context['can_submit_feedback'])

    def test_feedback_thanks_flag_from_query_param(self):
        event = _make_past_event()
        response = self.client.get(
            event.get_absolute_url() + '?feedback=thanks',
        )
        self.assertTrue(response.context['feedback_thanks'])

        response = self.client.get(event.get_absolute_url())
        self.assertFalse(response.context['feedback_thanks'])


# --- Template rendering tests ---


@tag('core')
class EventDetailFeedbackTemplateTest(TestCase):
    """The public event detail template renders the right surface."""

    @classmethod
    def setUpTestData(cls):
        cls.attendee = User.objects.create_user(
            email='att@test.com', password='pw',
        )

    def test_upcoming_event_renders_no_feedback_section(self):
        event = _make_upcoming_event()
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-feedback-section"')

    def test_past_event_zero_ratings_no_badge(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(event.get_absolute_url())
        # Section renders for a registered attendee on a past event.
        self.assertContains(response, 'data-testid="event-feedback-section"')
        # Aggregate badge does NOT render (no rated entries).
        self.assertNotContains(
            response, 'data-testid="event-feedback-aggregate"',
        )
        # Form renders.
        self.assertContains(response, 'data-testid="event-feedback-form"')
        self.assertContains(response, 'Submit feedback')

    def test_past_event_with_ratings_shows_badge_and_form(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='r1@t.com', password='pw'),
            rating=4,
        )
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-feedback-aggregate"')
        self.assertContains(response, 'data-testid="event-feedback-avg"')
        self.assertContains(response, 'data-testid="event-feedback-form"')

    def test_anonymous_on_past_event_sees_badge_not_form(self):
        event = _make_past_event()
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='r1@t.com', password='pw'),
            rating=5,
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-feedback-aggregate"')
        self.assertNotContains(response, 'data-testid="event-feedback-form"')

    def test_non_registered_authenticated_sees_badge_not_form(self):
        event = _make_past_event()
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='r1@t.com', password='pw'),
            rating=3,
        )
        # ``attendee`` exists but isn't registered.
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-feedback-aggregate"')
        self.assertNotContains(response, 'data-testid="event-feedback-form"')

    def test_update_button_label_when_already_submitted(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        EventFeedback.objects.create(
            event=event, user=self.attendee,
            rating=3, comment='ok',
        )
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'Update feedback')
        self.assertNotContains(response, '>Submit feedback<')

    def test_thanks_block_on_redirect_query(self):
        event = _make_past_event()
        EventRegistration.objects.create(event=event, user=self.attendee)
        self.client.login(email='att@test.com', password='pw')
        response = self.client.get(
            event.get_absolute_url() + '?feedback=thanks',
        )
        self.assertContains(response, 'data-testid="event-feedback-thanks"')

    def test_pluralized_rating_label(self):
        """Aggregate label uses singular/plural correctly."""
        event = _make_past_event()
        # One rated entry → "1 rating"
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='r1@t.com', password='pw'),
            rating=4,
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '1 rating')
        # 2 rated entries → "2 ratings"
        EventFeedback.objects.create(
            event=event,
            user=User.objects.create_user(email='r2@t.com', password='pw'),
            rating=5,
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '2 ratings')
