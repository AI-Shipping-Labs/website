"""Tests for the ``/events/<id>/<slug>`` canonical URL pattern (issue #673).

Covers:
- Canonical URL returns 200.
- Missing slug (``/events/<id>`` and ``/events/<id>/``) returns a 301
  redirect to the canonical id+slug URL.
- Wrong slug (``/events/<id>/wrong-slug``) returns a 301 redirect to
  the canonical id+slug URL.
- Old slug-only URLs (``/events/<slug>``) return 404 — the legacy
  fallback view was intentionally NOT added (operator-managed
  ``Redirect`` rows handle the one currently-active event).
- ``Event.save()`` truncates slug to 70 chars on the last ``-`` boundary.
- Two events with the same title produce different canonical URLs.
- Renaming an event mints a new canonical URL.
"""

from django.test import TestCase
from django.utils import timezone

from events.models import Event


class EventCanonicalUrlTest(TestCase):
    """Canonical ``/events/<id>/<slug>`` URL pattern (issue #673)."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='My Live Q&A',
            slug='my-live-qa',
            start_datetime=timezone.now(),
            status='upcoming',
        )

    def test_canonical_url_returns_200(self):
        """``/events/<id>/<slug>`` with the correct slug returns 200."""
        response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_detail.html')
        self.assertContains(response, 'My Live Q&amp;A')

    def test_get_absolute_url_format(self):
        """Helper emits ``/events/<id>/<slug>`` shape."""
        self.assertEqual(
            self.event.get_absolute_url(),
            f'/events/{self.event.id}/my-live-qa',
        )

    def test_missing_slug_redirects_to_canonical(self):
        """``/events/<id>`` (no slug) 301s to the canonical URL."""
        response = self.client.get(f'/events/{self.event.id}')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.event.get_absolute_url())

    def test_missing_slug_with_trailing_slash_redirects_to_canonical(self):
        """``/events/<id>/`` (trailing slash, no slug) 301s to canonical.

        The site-wide ``RemoveTrailingSlashMiddleware`` strips the
        trailing slash first (one 301), then ``event_detail_no_slug``
        redirects to the id+slug canonical URL (a second 301). Both
        hops are 301 so search engines collapse the chain.
        ``follow=True`` lets the test walk the chain and assert the
        final canonical URL.
        """
        response = self.client.get(
            f'/events/{self.event.id}/', follow=True,
        )
        self.assertEqual(response.status_code, 200)
        # Two hops of 301: trailing-slash stripper, then no-slug
        # redirect to canonical.
        chain = response.redirect_chain
        self.assertEqual(len(chain), 2, chain)
        self.assertEqual(chain[0][1], 301)
        self.assertEqual(chain[1][1], 301)
        self.assertEqual(chain[-1][0], self.event.get_absolute_url())

    def test_wrong_slug_redirects_to_canonical(self):
        """``/events/<id>/wrong-slug`` 301s to the canonical id+slug URL.

        This is the rename-survives-old-links guarantee: an old share
        of ``/events/42/q-and-a-april`` keeps resolving after the event
        is renamed to ``q-and-a-may``.
        """
        response = self.client.get(
            f'/events/{self.event.id}/wrong-slug-from-an-old-tweet',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.event.get_absolute_url())

    def test_old_slug_only_url_returns_404(self):
        """``/events/<slug>`` (legacy 2-segment URL) returns 404.

        We deliberately did not add a slug-only fallback view (operator
        adds a manual ``Redirect`` row in Studio for the one currently
        active event). New routes are 3-segment id+slug or
        sibling-suffix only, so a 2-segment slug lookup has no pattern
        to match.
        """
        response = self.client.get('/events/my-live-qa')
        self.assertEqual(response.status_code, 404)

    def test_unknown_id_returns_404(self):
        """An unknown id 404s — never silently redirects to homepage."""
        response = self.client.get('/events/999999/anything')
        self.assertEqual(response.status_code, 404)

    def test_unknown_id_without_slug_returns_404(self):
        """``/events/<unknown-id>`` 404s rather than redirecting nowhere."""
        response = self.client.get('/events/999999')
        self.assertEqual(response.status_code, 404)


class EventSlugTruncationTest(TestCase):
    """Issue #673: ``Event.save()`` truncates slug to 70 chars."""

    def test_short_slug_is_unchanged(self):
        event = Event.objects.create(
            title='Short', slug='short-slug',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.slug, 'short-slug')

    def test_exactly_70_chars_is_unchanged(self):
        slug_70 = 'a' * 70
        event = Event.objects.create(
            title='At Cap', slug=slug_70,
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.slug, slug_70)
        self.assertEqual(len(event.slug), 70)

    def test_long_slug_truncated_to_70_or_fewer(self):
        """A 200-char source slug is capped at 70 chars."""
        long_slug = '-'.join(['word'] * 50)  # ~250 chars with dashes
        event = Event.objects.create(
            title='Long', slug=long_slug,
            start_datetime=timezone.now(),
        )
        self.assertLessEqual(len(event.slug), 70)
        # No trailing dash from a mid-word cut.
        self.assertFalse(event.slug.endswith('-'))

    def test_truncation_breaks_at_last_dash(self):
        """If 70 lands mid-word, walk back to the previous ``-``."""
        # ``aa-bb-...-zz`` style; the 70th char will be inside a word
        # so the helper walks back to the previous boundary.
        slug = '-'.join(['aaaa'] * 30)
        event = Event.objects.create(
            title='Slug', slug=slug,
            start_datetime=timezone.now(),
        )
        # Each piece is whole — no half-clipped word at the tail.
        for piece in event.slug.split('-'):
            self.assertEqual(piece, 'aaaa')
        self.assertLessEqual(len(event.slug), 70)

    def test_truncation_does_not_emit_empty_slug(self):
        """Defensive: a single huge word with no dashes still saves."""
        no_dashes = 'x' * 200
        event = Event.objects.create(
            title='No Dashes', slug=no_dashes,
            start_datetime=timezone.now(),
        )
        # Falls back to the un-truncated slug rather than ''.
        self.assertTrue(event.slug)


class EventCanonicalUrlPerInstanceTest(TestCase):
    """Two events with the same title resolve to different canonical URLs."""

    def test_same_title_yields_different_canonical_urls(self):
        """Same title, different events => different ``get_absolute_url``.

        Slug collisions are caught at the DB level (``SlugField`` is
        ``unique=True``), so we mint different slugs but the same title
        and assert the URLs differ on id, not slug.
        """
        first = Event.objects.create(
            title='Live Q&A',
            slug='live-qa',
            start_datetime=timezone.now(),
        )
        second = Event.objects.create(
            title='Live Q&A',
            slug='live-qa-2',
            start_datetime=timezone.now(),
        )
        self.assertNotEqual(
            first.get_absolute_url(), second.get_absolute_url(),
        )
        self.assertIn(str(first.id), first.get_absolute_url())
        self.assertIn(str(second.id), second.get_absolute_url())


class EventRenameEmitsNewSlugTest(TestCase):
    """Renaming an event's slug changes the canonical URL it emits."""

    def test_rename_changes_get_absolute_url(self):
        """``Event.get_absolute_url`` reflects the current slug."""
        event = Event.objects.create(
            title='Q and A',
            slug='q-and-a-april',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        before = event.get_absolute_url()

        event.slug = 'q-and-a-may'
        event.save()
        after = event.get_absolute_url()

        self.assertNotEqual(before, after)
        self.assertIn('q-and-a-may', after)
        self.assertNotIn('q-and-a-april', after)
        # ID is unchanged, so the prefix is the same.
        self.assertEqual(before.split('/')[2], after.split('/')[2])

    def test_old_canonical_url_with_stale_slug_redirects(self):
        """``/events/<id>/<old-slug>`` 301s to the renamed canonical URL.

        This is the link-survives-rename invariant: a Slack share with
        the old slug still resolves to the new event page.
        """
        event = Event.objects.create(
            title='Q and A',
            slug='q-and-a-april',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        stale_url = event.get_absolute_url()
        event.slug = 'q-and-a-may'
        event.save()

        response = self.client.get(stale_url)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], event.get_absolute_url(),
        )
