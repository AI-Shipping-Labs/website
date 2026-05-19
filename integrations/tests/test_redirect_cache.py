"""Tests for the redirect cache helpers in ``integrations.middleware``.

These tests guard the cross-process semantics added in issue #695. The
redirect cache used to live in the default ``cache`` (``LocMemCache``),
which is per-process. Each gunicorn worker kept its own copy, so a
Studio toggle / delete only invalidated the worker that handled the
POST and every other worker kept serving the stale snapshot for up to
``REDIRECT_CACHE_TIMEOUT``. We now use ``caches['django_q']``
(``DatabaseCache`` in prod, ``LocMemCache`` in tests, but a separate
named backend either way) so an invalidation is visible to every
caller.
"""

from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import TestCase

from integrations.middleware import (
    REDIRECT_CACHE_KEY,
    clear_redirect_cache,
    get_active_redirects,
)
from integrations.models import Redirect


class RedirectCacheBackendTest(TestCase):
    """The cache lives on ``caches['django_q']``, not the default cache."""

    def setUp(self):
        clear_redirect_cache()
        default_cache.delete(REDIRECT_CACHE_KEY)

    def tearDown(self):
        clear_redirect_cache()
        default_cache.delete(REDIRECT_CACHE_KEY)

    def test_populate_writes_to_django_q_cache(self):
        Redirect.objects.create(
            source_path='/old',
            target_path='/new',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()

        # Prime the cache.
        get_active_redirects()

        # The key must land on the cross-process cache.
        self.assertIsNotNone(caches['django_q'].get(REDIRECT_CACHE_KEY))

    def test_populate_does_not_write_to_default_cache(self):
        Redirect.objects.create(
            source_path='/old',
            target_path='/new',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()

        # Prime the cache.
        get_active_redirects()

        # Guard against accidental regression to the per-process default.
        self.assertIsNone(default_cache.get(REDIRECT_CACHE_KEY))

    def test_clear_invalidates_django_q_cache(self):
        Redirect.objects.create(
            source_path='/old',
            target_path='/new',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()
        get_active_redirects()
        self.assertIsNotNone(caches['django_q'].get(REDIRECT_CACHE_KEY))

        clear_redirect_cache()

        self.assertIsNone(caches['django_q'].get(REDIRECT_CACHE_KEY))


class RedirectCacheInvalidationPropagationTest(TestCase):
    """A clear from one caller must be visible to a separate reader.

    This mirrors the gunicorn-multi-worker scenario the per-process
    ``LocMemCache`` got wrong: writer worker invalidates, but reader
    worker keeps serving the stale snapshot.
    """

    def setUp(self):
        clear_redirect_cache()

    def tearDown(self):
        clear_redirect_cache()

    def test_toggle_to_inactive_propagates_after_clear(self):
        redirect = Redirect.objects.create(
            source_path='/toggle-test',
            target_path='/elsewhere',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()

        # Reader primes the cache and sees the active redirect.
        redirects = get_active_redirects()
        self.assertIn('/toggle-test', redirects)

        # Writer toggles via the ORM (Studio toggle does the same thing).
        Redirect.objects.filter(pk=redirect.pk).update(is_active=False)

        # Without invalidation the cache is still stale.
        self.assertIn('/toggle-test', get_active_redirects())

        # Writer invalidates the shared cache.
        clear_redirect_cache()

        # Reader now refetches and sees the new state.
        self.assertNotIn('/toggle-test', get_active_redirects())

    def test_delete_propagates_after_clear(self):
        redirect = Redirect.objects.create(
            source_path='/delete-test',
            target_path='/elsewhere',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()

        redirects = get_active_redirects()
        self.assertIn('/delete-test', redirects)

        Redirect.objects.filter(pk=redirect.pk).delete()
        clear_redirect_cache()

        self.assertNotIn('/delete-test', get_active_redirects())

    def test_new_redirect_visible_after_clear(self):
        # Prime the cache with the empty (or seeded) state.
        clear_redirect_cache()
        before = get_active_redirects()
        self.assertNotIn('/brand-new', before)

        Redirect.objects.create(
            source_path='/brand-new',
            target_path='/somewhere',
            redirect_type=302,
            is_active=True,
        )
        clear_redirect_cache()

        after = get_active_redirects()
        self.assertIn('/brand-new', after)
        self.assertEqual(after['/brand-new'], ('/somewhere', 302))
