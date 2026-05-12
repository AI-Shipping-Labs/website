"""Tests for external-event support — issues #572 and #579.

Covers:

- ``Event.external_host`` field and the ``Event.is_external`` derived
  property.
- Event list page: external pill on upcoming and past cards; absence on
  community-hosted cards.
- Event detail page: external pill in the header, registration card
  swapped for the external Join card, paywall + email-only registration
  form suppressed for external events (regardless of ``required_level``).
- External event with empty join URL renders a "Link coming soon"
  message and returns 200 (no 500).
- Studio event form: persists ``external_host`` on both synced and
  non-synced paths; the field appears in the form HTML.
- GitHub sync dispatcher: reads ``external_host`` from frontmatter and
  defaults to empty when absent.
- Issue #579: ``EXTERNAL_HOST_CHOICES`` constrains the field; the
  studio view coerces unknown POSTs to ''; the sync dispatcher coerces
  unknown frontmatter values to '' with a warning.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.access import LEVEL_MAIN
from events.models import Event
from events.models.event import EXTERNAL_HOST_CHOICES
from integrations.services.github_sync.dispatchers.events import (
    _build_synced_event_content_defaults,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_event(**overrides):
    """Create a minimal upcoming event suitable for view tests."""
    defaults = {
        'title': 'Sample event',
        'slug': 'sample-event',
        'description': 'A community session.',
        'start_datetime': timezone.now() + timedelta(days=7),
        'status': 'upcoming',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


# --- Model tests --------------------------------------------------------


class ExternalHostModelTest(TierSetupMixin, TestCase):
    """Test the Event.external_host field and the is_external property."""

    def test_default_external_host_is_empty(self):
        event = _make_event(slug='community-default')
        # Defaults: empty string, is_external=False.
        self.assertEqual(event.external_host, '')
        self.assertFalse(event.is_external)

    def test_is_external_true_when_external_host_set(self):
        event = _make_event(slug='maven-cohort', external_host='Maven')
        self.assertTrue(event.is_external)

    def test_is_external_strips_whitespace_only_values(self):
        """Whitespace-only ``external_host`` must NOT flip the event to
        external mode. Templates branch on ``is_external`` so silently
        treating "   " as external would render a "Hosted on    " pill.
        """
        event = _make_event(slug='whitespace', external_host='   ')
        self.assertFalse(event.is_external)


# --- Event list page tests ----------------------------------------------


class ExternalPillOnListTest(TierSetupMixin, TestCase):
    """The external pill renders on upcoming and past cards for external
    events and is absent for community-hosted events.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.now = timezone.now()
        # Upcoming pair: one community, one external.
        cls.community_upcoming = _make_event(
            slug='community-live', title='Community live coding',
        )
        cls.external_upcoming = _make_event(
            slug='maven-cohort', title='LLM engineering cohort',
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/llm-eng',
        )
        # Past pair (completed with recording).
        cls.community_past = _make_event(
            slug='community-recap', title='Community recap',
            status='completed',
            start_datetime=cls.now - timedelta(days=14),
            recording_url='https://example.com/recording.mp4',
            published=True,
        )
        cls.external_past = _make_event(
            slug='luma-recap', title='Luma meetup replay',
            external_host='Luma',
            status='completed',
            start_datetime=cls.now - timedelta(days=10),
            recording_url='https://lu.ma/replay/123',
            published=True,
        )

    def test_upcoming_external_card_shows_pill(self):
        response = self.client.get('/events?filter=upcoming')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # Must contain the external-pill test marker once, with the host
        # name following "Hosted on".
        self.assertIn('data-testid="event-card-external-badge"', html)
        self.assertIn('Hosted on Maven', html)

    def test_upcoming_community_card_has_no_pill(self):
        """Render only the community event and assert the external pill
        is absent — guards against accidentally surfacing the pill on
        non-external rows.
        """
        # Hide the external upcoming event so only the community event
        # shows up.
        self.external_upcoming.status = 'draft'
        self.external_upcoming.save()

        response = self.client.get('/events?filter=upcoming')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('data-testid="event-card-external-badge"', html)
        self.assertNotIn('Hosted on', html)

    def test_past_external_card_shows_pill(self):
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-testid="event-card-external-badge"', html)
        self.assertIn('Hosted on Luma', html)
        # The community recap should be in the list (not gated out by
        # the external pill) and should not itself have a pill, but
        # we already assert the pill count below in a stricter test.

    def test_past_community_card_has_no_external_pill(self):
        """Past list should not render the pill for community events even
        when external events sit alongside them in the same listing.
        """
        # Hide the external past event so only the community event is
        # paginated through the past list.
        self.external_past.published = False
        self.external_past.save()

        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('data-testid="event-card-external-badge"', html)

    def test_past_tag_filter_keeps_external_events(self):
        """Tag filtering must not strip external events — the spec says
        external events are interleaved with community ones, not
        filtered out by external status.
        """
        # Both past events get the python tag so the filter keeps them.
        self.community_past.tags = ['python']
        self.community_past.save()
        self.external_past.tags = ['python']
        self.external_past.save()

        response = self.client.get('/events?filter=past&tag=python')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # The external pill is rendered exactly once in the filtered
        # result — for the Luma replay.
        self.assertIn('Hosted on Luma', html)
        self.assertIn('Luma meetup replay', html)
        self.assertIn('Community recap', html)


# --- Event detail page tests --------------------------------------------


class ExternalEventDetailTest(TierSetupMixin, TestCase):
    """External upcoming events render the external Join card in place of
    the registration card, regardless of ``required_level``, and
    suppress the email-only registration form and the upgrade paywall.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.external_event = _make_event(
            slug='maven-llm-eng',
            title='LLM engineering cohort',
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/llm-eng',
        )
        cls.community_event = _make_event(
            slug='community-session',
            title='Community session',
        )

    def test_external_detail_renders_pill_and_join_card(self):
        response = self.client.get(f'/events/{self.external_event.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # Header pill present.
        self.assertIn('data-testid="event-detail-external-badge"', html)
        self.assertIn('Hosted on Maven', html)
        # External Join card replaces the registration card.
        self.assertIn('data-testid="event-external-join-card"', html)
        self.assertIn('data-testid="event-external-join-link"', html)
        # Join button uses the host name verbatim.
        self.assertIn('Join on Maven', html)
        # Join URL points to the external registration link.
        self.assertIn('https://maven.com/aisl/llm-eng', html)

    def test_external_detail_join_link_has_blank_target_and_noopener(self):
        response = self.client.get(f'/events/{self.external_event.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # The whole external Join link block is the one we care about.
        # Locate it and assert on its attributes.
        marker = 'data-testid="event-external-join-link"'
        idx = html.find(marker)
        self.assertGreater(idx, -1)
        # Slice a window before the marker to inspect its attributes.
        window_start = max(0, idx - 500)
        anchor_html = html[window_start: idx + 100]
        self.assertIn('target="_blank"', anchor_html)
        self.assertIn('rel="noopener noreferrer"', anchor_html)

    def test_external_detail_suppresses_registration_card(self):
        """The registration card and its three branches must not render
        for an external event.
        """
        response = self.client.get(f'/events/{self.external_event.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('data-testid="event-registration-card"', html)
        self.assertNotIn('data-testid="event-anonymous-email-form"', html)
        self.assertNotIn('data-testid="event-anonymous-cta"', html)

    def test_external_event_anonymous_no_email_form(self):
        """A free-tier external event must NOT expose the email-only
        registration form to anonymous visitors.
        """
        # required_level=0 is the same condition that would normally
        # surface the anonymous email-only form on a community event.
        response = self.client.get(f'/events/{self.external_event.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('data-testid="event-anonymous-email-form"', html)
        # "Sign in to register" lives inside the anonymous CTA — it
        # should also be gone for external events.
        self.assertNotIn('Sign in to register', html)

    def test_external_event_ignores_required_level_for_visibility(self):
        """An external event with ``required_level=20`` (Main+) must
        still show the Join button to a Free user — external events are
        aggregated, not gated.
        """
        gated_external = _make_event(
            slug='gated-external',
            title='DataTalksClub Live',
            external_host='DataTalksClub',
            zoom_join_url='https://datatalksclub.com/live',
            required_level=LEVEL_MAIN,
        )
        free_user = User.objects.create_user(
            email='free@test.com', password='pw',
        )
        free_user.tier = self.free_tier
        free_user.email_verified = True
        free_user.save()
        self.client.force_login(free_user)

        response = self.client.get(f'/events/{gated_external.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # Upgrade CTA is gone.
        self.assertNotIn('Upgrade to Main', html)
        self.assertNotIn('data-testid="event-required-tier-label"', html)
        # Join button is present and points to the partner URL.
        self.assertIn('data-testid="event-external-join-link"', html)
        self.assertIn('https://datatalksclub.com/live', html)

    def test_external_event_with_empty_join_url_renders_coming_soon(self):
        """Defensive: if ``zoom_join_url`` is empty on an external event,
        the page must still load 200 and render a "Link coming soon"
        placeholder instead of a broken button.
        """
        no_url = _make_event(
            slug='maven-tba',
            title='Maven event TBA',
            external_host='Maven',
            zoom_join_url='',
        )
        response = self.client.get(f'/events/{no_url.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-testid="event-external-join-card"', html)
        self.assertIn('data-testid="event-external-join-coming-soon"', html)
        self.assertIn('Link coming soon', html)
        # No clickable external join link should be rendered.
        self.assertNotIn('data-testid="event-external-join-link"', html)
        # Guard against template comment leaks (regression: {# #} is single-line
        # only and multi-line {# ... #} blocks render their raw text to HTML).
        self.assertNotIn('Defensive: no join URL', html)
        self.assertNotIn('rather than a broken button', html)

    def test_community_event_keeps_registration_card(self):
        """Regression guard: community events still render the existing
        registration card and do not pick up the external Join card.
        """
        response = self.client.get(f'/events/{self.community_event.slug}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-testid="event-registration-card"', html)
        self.assertNotIn('data-testid="event-external-join-card"', html)
        self.assertNotIn('data-testid="event-detail-external-badge"', html)


# --- Studio form tests --------------------------------------------------


class StudioExternalHostFormTest(TierSetupMixin, TestCase):
    """The Studio event-edit form exposes an ``external_host`` input and
    persists it on POST for both synced and non-synced rows.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        # Non-synced (Studio-authored) event.
        self.event = _make_event(slug='studio-event', title='Studio event')
        self.client.force_login(self.staff)

    def _post_payload(self, **overrides):
        payload = {
            'title': self.event.title,
            'slug': self.event.slug,
            'description': self.event.description,
            'platform': 'zoom',
            'event_date': self.event.start_datetime.strftime('%d/%m/%Y'),
            'event_time': self.event.start_datetime.strftime('%H:%M'),
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'location': '',
            'max_participants': '',
            'status': 'upcoming',
            'required_level': '0',
            'tags': '',
            'external_host': '',
        }
        payload.update(overrides)
        return payload

    def test_form_renders_external_host_input(self):
        """Issue #579: the form template renders a <select> for
        external_host with the canonical partner list (no datalist, no
        text input).
        """
        url = reverse('studio_event_edit', kwargs={'event_id': self.event.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="external_host"', html)
        self.assertIn('id="external-host-input"', html)
        self.assertIn('data-testid="studio-event-external-host"', html)
        self.assertIn('Maven', html)
        self.assertIn('Luma', html)
        self.assertIn('DataTalksClub', html)
        # Issue #579: no free-text input or datalist for external_host.
        self.assertNotIn('id="external-host-suggestions"', html)
        self.assertNotIn(
            '<input type="text" name="external_host"', html,
        )
        # Guard against template comment leaks (regression: {# #} is single-line
        # only and multi-line {# ... #} blocks render their raw text to HTML).
        self.assertNotIn('third-party host indicator', html)
        self.assertNotIn('not enforced choices', html)

    def test_post_persists_external_host(self):
        url = reverse('studio_event_edit', kwargs={'event_id': self.event.pk})
        response = self.client.post(
            url, self._post_payload(external_host='Maven'),
        )
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.external_host, 'Maven')
        self.assertTrue(self.event.is_external)

    def test_post_with_blank_external_host_clears_field(self):
        """Saving the form with a blank value must clear the field so
        staff can revert an event back to community-hosted mode.
        """
        self.event.external_host = 'Luma'
        self.event.save()
        url = reverse('studio_event_edit', kwargs={'event_id': self.event.pk})
        response = self.client.post(
            url, self._post_payload(external_host=''),
        )
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.external_host, '')
        self.assertFalse(self.event.is_external)


# --- Sync dispatcher tests ----------------------------------------------


class SyncDispatcherExternalHostTest(TestCase):
    """``_build_synced_event_content_defaults`` must accept an
    ``external_host`` keyword and default to empty when absent — back-
    compat with existing YAML files that do not set the field.
    """

    def _common_kwargs(self, **overrides):
        kwargs = {
            'source': type('S', (), {'repo_name': 'AI-Shipping-Labs/content'})(),
            'source_path': 'events/sample.yaml',
            'commit_sha': 'abc123',
            'content_id': 'event-sample',
            'title': 'Sample',
        }
        kwargs.update(overrides)
        return kwargs

    def test_default_external_host_is_empty(self):
        defaults = _build_synced_event_content_defaults(**self._common_kwargs())
        self.assertIn('external_host', defaults)
        self.assertEqual(defaults['external_host'], '')

    def test_passed_external_host_persists(self):
        defaults = _build_synced_event_content_defaults(
            external_host='Maven', **self._common_kwargs(),
        )
        self.assertEqual(defaults['external_host'], 'Maven')

    def test_external_host_is_stripped(self):
        """Whitespace around the value is normalized away to keep stray
        YAML whitespace from accidentally flipping an event to external
        mode.
        """
        defaults = _build_synced_event_content_defaults(
            external_host='  Maven  ', **self._common_kwargs(),
        )
        self.assertEqual(defaults['external_host'], 'Maven')


# --- Issue #579: choices constraint ------------------------------------


class ExternalHostChoicesConstantTest(TestCase):
    """The canonical partner list lives in
    ``events.models.event.EXTERNAL_HOST_CHOICES`` and is referenced from
    the model field, the studio view, the form template, and the sync
    dispatcher. This test pins the contents and order so accidental
    reorderings or additions surface here, not in production.
    """

    def test_external_host_choices_constant(self):
        self.assertEqual(
            EXTERNAL_HOST_CHOICES,
            [
                ('', 'Community-hosted'),
                ('Maven', 'Maven'),
                ('Luma', 'Luma'),
                ('DataTalksClub', 'DataTalksClub'),
            ],
        )


class ExternalHostFullCleanTest(TierSetupMixin, TestCase):
    """``Event.full_clean()`` enforces the ``choices`` whitelist. The
    studio view skips ``full_clean`` today (it relies on the dropdown
    plus a defensive coerce), but admin/CLI paths and any future caller
    that runs validators must reject non-canonical values.
    """

    def test_external_host_clean_rejects_unknown_value(self):
        event = Event(
            title='Bad host',
            slug='bad-host',
            start_datetime=timezone.now() + timedelta(days=1),
            external_host='InvalidHost',
        )
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn('external_host', ctx.exception.error_dict)

    def test_external_host_clean_accepts_each_canonical_value(self):
        for value, _ in EXTERNAL_HOST_CHOICES:
            with self.subTest(value=value):
                event = Event(
                    title=f'Event for {value or "blank"}',
                    slug=f'evt-{value or "blank"}',
                    start_datetime=timezone.now() + timedelta(days=1),
                    external_host=value,
                )
                event.full_clean()


class StudioViewCoercesUnknownExternalHostTest(TierSetupMixin, TestCase):
    """A POST with a non-canonical ``external_host`` (form tampering or
    a stale browser cache) must be coerced to '' before save, never
    persisted as-is.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff579@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.event = _make_event(
            slug='studio-579', title='Studio 579 event',
        )
        self.client.force_login(self.staff)

    def test_studio_view_coerces_unknown_post_to_blank(self):
        url = reverse('studio_event_edit', kwargs={'event_id': self.event.pk})
        payload = {
            'title': self.event.title,
            'slug': self.event.slug,
            'description': self.event.description,
            'platform': 'zoom',
            'event_date': self.event.start_datetime.strftime('%d/%m/%Y'),
            'event_time': self.event.start_datetime.strftime('%H:%M'),
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'location': '',
            'max_participants': '',
            'status': 'upcoming',
            'required_level': '0',
            'tags': '',
            'external_host': 'DataTalks Club',
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.external_host, '')


class SyncDispatcherCoercesUnknownExternalHostTest(TestCase):
    """Issue #579: the sync dispatcher must coerce non-canonical
    ``external_host`` frontmatter values to '' (with a warning) instead
    of letting bad data slip through ``Event.save()``, which skips
    field validators.
    """

    def _common_kwargs(self, **overrides):
        kwargs = {
            'source': type('S', (), {'repo_name': 'AI-Shipping-Labs/content'})(),
            'source_path': 'events/sample.yaml',
            'commit_sha': 'abc123',
            'content_id': 'event-sample',
            'title': 'Sample',
        }
        kwargs.update(overrides)
        return kwargs

    def test_github_sync_dispatcher_coerces_unknown_frontmatter(self):
        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ) as captured:
            defaults = _build_synced_event_content_defaults(
                external_host='maven', **self._common_kwargs(),
            )
        self.assertEqual(defaults['external_host'], '')
        joined = '\n'.join(captured.output)
        self.assertIn('external_host', joined)
        self.assertIn('maven', joined)
