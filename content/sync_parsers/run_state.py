"""Per-sync run state shared by the site content parsers.

The package engine drives one parser family after another within a single
checkout session. Cross-family artifacts computed once per sync (repo
classification, known image set, S3 media pre-pass) and per-family sync
bookkeeping (seen/failed semantic ids, bounded error sinks) live here, keyed
by the active checkout object.
"""

import threading
import weakref

from content.sync_parsers.checkout_view import (
    CheckoutView,
    activate_view,
)
from content.sync_parsers.families.classify import classify_checkout

_LOCAL = threading.local()
_COLLECTOR = threading.local()

# One sync = one checkout object; the registry keeps the run alive across the
# package engine's per-family calls and dies with the checkout itself.
_RUNS = weakref.WeakKeyDictionary()


def set_results_collector(fn):
    """Bind the callable receiving ``(family, details)`` per family completion."""
    _COLLECTOR.fn = fn


def emit_results(family, details):
    fn = getattr(_COLLECTOR, 'fn', None)
    if fn is not None and details:
        fn(family, list(details))


def emit_errors(family, errors):
    """Publish one family's rich per-file errors to the collector."""
    fn = getattr(_COLLECTOR, 'fn_errors', None)
    if fn is not None and errors:
        fn(family, list(errors))


def set_errors_collector(fn):
    _COLLECTOR.fn_errors = fn


def emit_extras(extras):
    """Publish run-level extras (tiers counters) to the collector."""
    fn = getattr(_COLLECTOR, 'fn_extras', None)
    if fn is not None and extras:
        fn(dict(extras))


def set_extras_collector(fn):
    _COLLECTOR.fn_extras = fn


def run_for(checkout, source):
    """Return the SyncRun bound to ``checkout``, creating it on first use."""
    run = _RUNS.get(checkout)
    if run is None:
        run = SyncRun(checkout, source)
        _RUNS[checkout] = run
    return run


class FamilyState:
    """Bookkeeping for one parser family during one sync run."""

    def __init__(self, name):
        self.name = name
        self.seen = set()
        self.failed = set()
        self.errors = []
        self.details = []
        self.state = {}

    def stats(self):
        """Legacy-shaped stats dict for moved dispatcher bodies."""
        return {
            'created': 0,
            'updated': 0,
            'unchanged': 0,
            'deleted': 0,
            'errors': [],
            'items_detail': [],
        }

    def record_error(self, error):
        self.errors.append(error)


class SyncRun:
    """State for one ``sync_content_source`` execution on one checkout."""

    def __init__(self, checkout, source):
        self.checkout = checkout
        self.source = source
        self.view = CheckoutView(checkout)
        self._classification = None
        self._known_images = None
        self._cross_workshop = None
        self._media_prepass = None
        self.families = {}

    @property
    def repo_dir(self):
        """Synthetic root handed to moved dispatcher bodies."""
        return self.view.root

    @property
    def commit_sha(self):
        return getattr(self.checkout, 'commit_sha', '') or ''

    def classification(self):
        if self._classification is None:
            self._classification = classify_checkout(self)
        return self._classification

    def classify_errors(self):
        """Classifier parse errors recorded as legacy error entries."""
        return getattr(self, '_classify_errors', [])

    def known_images(self):
        if self._known_images is None:
            self._known_images = frozenset(self.view.image_paths())
        return self._known_images

    def family(self, name):
        if name not in self.families:
            self.families[name] = FamilyState(name)
        return self.families[name]

    def record_s3_errors(self, errors):
        self._media_prepass = self._media_prepass or {'uploaded': 0, 'skipped': 0, 'errors': []}
        self._media_prepass['errors'].extend(errors)

    def record_media_stats(self, stats):
        self._media_prepass = stats

    def ensure_media_prepass(self):
        """Run the S3 image pre-pass once per sync (legacy pipeline order).

        The old engine uploaded every repo image before dispatch. Under the
        package orchestration the first parser's discover triggers the same
        bulk pass; stats/errors are recorded for the runner and the first
        family's bounded error.
        """
        if self._media_prepass is not None:
            return
        from content.sync_parsers.media import upload_images_to_s3

        self._media_prepass = upload_images_to_s3(self.repo_dir, self.source)

    def drain_classify_errors(self):
        errors = getattr(self, '_classify_errors', None)
        if errors:
            self._classify_errors = []
        return errors or []

    def media_stats(self):
        return self._media_prepass

    def cross_workshop_lookup(self, errors=None):
        if self._cross_workshop is None:
            from content.sync_parsers.families.classify import build_cross_workshop_lookup

            self._cross_workshop = build_cross_workshop_lookup(
                self.classification().workshop_dirs, self.repo_dir, errors,
            )
        return self._cross_workshop


def current_run():
    run = getattr(_LOCAL, 'run', None)
    if run is not None:
        return run
    # The package engine interleaves discover/upsert/soft_delete per parser;
    # the run bound by the most recent discover on this thread is current.
    return getattr(_LOCAL, 'last_run', None)


def activate(run):
    """Bind the run and its checkout view for the current thread."""
    class _Scope:
        def __enter__(self):
            self.view_scope = activate_view(run.view)
            self.token = getattr(_LOCAL, 'run', None)
            self.view_scope.__enter__()
            _LOCAL.run = run
            return run

        def __exit__(self, exc_type, exc, traceback):
            self.view_scope.__exit__(exc_type, exc, traceback)
            if _LOCAL.run is run:
                _LOCAL.last_run = run
                _LOCAL.run = None
            else:
                _LOCAL.run = self.token
            return False

    return _Scope()
