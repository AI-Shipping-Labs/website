"""Custom Django test runner that fixes the ``--parallel`` pickling bug.

Django's parallel test runner serialises results (including tracebacks)
across worker processes via ``multiprocessing.Pool``. Python's stdlib
``traceback`` objects are NOT picklable because they reference frame
objects, which in turn reference locals and code objects that may
contain unpicklable things. When a parallel worker hits its first
failure, the runner crashes with ``TypeError: cannot pickle 'traceback'
object`` — the parent process never sees the failure, every follow-on
failure on every worker is silently masked, and the job sits idle until
the worker timeout fires.

Bug history: Django ticket #29023 and its successors. The issue has
been reopened across multiple releases; pinning a Django version isn't
a stable fix.

Fix: ``tblib.pickling_support.install()`` monkey-patches the traceback
type with ``__reduce__`` / ``__setstate__`` methods so tracebacks pickle
cleanly. Call it once at runner construction so every worker process
inherits the patch.

This lets us keep ``--parallel`` in CI (fast happy path) while also
surfacing every failure in one cycle (fail-not-fast — the parent
collects all worker results instead of crashing on the first).
"""

from __future__ import annotations

import hashlib
import os

import django
import tblib.pickling_support
from django.test import override_settings
from django.test.runner import (
    DiscoverRunner,
    ParallelTestSuite,
    partition_suite_by_case,
)
from django.test.utils import iter_test_cases

tblib.pickling_support.install()


# The production compatibility window for legacy numeric Stripe checkout
# references closed on 2026-08-01.  Many older webhook fixtures intentionally
# still exercise that compatibility path, so running them against wall-clock
# time made the suite turn red the day after the cutoff.  Give the test suite a
# stable pre-cutoff configuration without weakening production behavior.
# Dedicated security tests override the config/clock explicitly to verify the
# real cutoff and kill-switch contracts.
TEST_LEGACY_NUMERIC_REFERENCE_CUTOFF = "2099-01-01T00:00:00Z"
TEST_PASSWORD_HASHERS = ("django.contrib.auth.hashers.MD5PasswordHasher",)
_ENV_UNSET = object()


class FastPasswordHasherParallelTestSuite(ParallelTestSuite):
    """Ensure spawned workers receive the runner's test-only hasher override.

    Linux's default ``fork`` workers inherit the override enabled by
    ``PicklableTracebackRunner.setup_test_environment``. Django's ``spawn``
    and ``forkserver`` paths start from freshly imported settings instead, so
    ``ParallelTestSuite`` calls this hook before worker setup in those modes.
    The worker process owns the override for its remaining lifetime.
    """

    def process_setup(*_args):
        # Django passes the unbound ``__func__`` to spawned workers, so this
        # hook must also be callable without the suite instance.
        django.setup()
        override_settings(PASSWORD_HASHERS=TEST_PASSWORD_HASHERS).enable()


# --- Deterministic test sharding (issue #888) -----------------------------
#
# The Django suite grew to ~9908 tests / ~1760s, creeping toward the CI
# job timeout. To get the suite *faster* (not just allowed to run longer),
# ``deploy-dev.yml`` and ``ci.yml`` fan the test job out across a matrix of
# N runners. Each shard runs a disjoint subset of the tests; the deploy job
# gates on every shard passing.
#
# How shards are assigned:
#   * ``TEST_SHARD_COUNT``  — total number of shards (N).
#   * ``TEST_SHARD_INDEX``  — this shard's 1-based index (1..N).
# A test is assigned to exactly one shard by hashing its stable test id
# (``module.Class.method``) and taking ``hash % N``. The hash is SHA-256 of
# the id, so the assignment is:
#   * deterministic   — same id always lands in the same shard across runs
#                       and across runners (no dependence on discovery order,
#                       PYTHONHASHSEED, or wall-clock);
#   * disjoint        — each id maps to exactly one shard, so summing the
#                       per-shard ``Ran N tests`` counts equals the single-job
#                       count (no test runs twice, none is dropped);
#   * balanced enough — SHA-256 spreads ids ~uniformly across the N buckets.
#
# When ``TEST_SHARD_COUNT`` is unset (or <= 1) the runner behaves exactly
# like the stock ``DiscoverRunner`` — i.e. local ``make test`` runs the whole
# suite in one process, unchanged. To change N, edit the ``shard`` matrix
# (and ``TEST_SHARD_COUNT``) in the workflow files; nothing here is pinned to
# a specific N.


def _shard_for_test_id(test_id: str, shard_count: int) -> int:
    """Return the 1-based shard index a test id deterministically maps to."""
    digest = hashlib.sha256(test_id.encode("utf-8")).hexdigest()
    return (int(digest, 16) % shard_count) + 1


def _read_shard_env() -> tuple[int, int] | None:
    """Parse ``TEST_SHARD_INDEX`` / ``TEST_SHARD_COUNT`` from the environment.

    Returns ``(index, count)`` when sharding is active, or ``None`` when it
    is disabled (count unset or <= 1). Raises ``ValueError`` on an invalid
    or out-of-range configuration so a typo in the workflow fails loudly
    instead of silently dropping or duplicating tests.
    """
    raw_count = os.environ.get("TEST_SHARD_COUNT")
    if raw_count is None or raw_count == "":
        return None
    count = int(raw_count)
    if count <= 1:
        return None
    raw_index = os.environ.get("TEST_SHARD_INDEX")
    if raw_index is None or raw_index == "":
        raise ValueError(
            "TEST_SHARD_COUNT is set but TEST_SHARD_INDEX is missing; "
            "set both (1-based index) or neither."
        )
    index = int(raw_index)
    if not 1 <= index <= count:
        raise ValueError(
            f"TEST_SHARD_INDEX={index} out of range for "
            f"TEST_SHARD_COUNT={count} (expected 1..{count})."
        )
    return index, count


class PicklableTracebackRunner(DiscoverRunner):
    """``DiscoverRunner`` with tblib pickling + deterministic sharding.

    tblib: installing tblib at module import time covers the case where a
    test is collected and run before this class is instantiated. We also
    re-install in ``__init__`` defensively in case the parent module was
    imported before tblib was available (e.g. in a partial install).

    Sharding: when ``TEST_SHARD_COUNT`` > 1, ``build_suite`` keeps only the
    tests assigned to ``TEST_SHARD_INDEX`` (see ``_shard_for_test_id``). This
    runs after tag filtering and reordering but before parallel partitioning,
    so each shard still parallelises across the runner's workers and the
    ``--exclude-tag=visual_regression`` filter is honoured per shard.

    Why we filter the *flat* TestCases (not the suite the parent returns):
    when ``--parallel`` > 1, ``DiscoverRunner.build_suite`` returns a
    ``ParallelTestSuite`` whose iteration yields nested ``TestSuite``
    subsuites, not flat ``TestCase`` objects — and ``TestSuite`` has no
    ``.id()``. Iterating the returned suite to read ``test.id()`` therefore
    raises ``AttributeError`` under the exact configuration CI uses
    (``--parallel 4``). Instead we flatten the suite back to its constituent
    ``TestCase``s with Django's own ``iter_test_cases`` (which works for both
    the flat and parallel-wrapped shapes), drop the ids that don't belong to
    this shard, then re-apply the parent's parallel-partitioning logic so the
    shard is parallelised exactly as an unsharded run would be.
    """

    parallel_test_suite = FastPasswordHasherParallelTestSuite

    def __init__(self, *args, **kwargs):
        tblib.pickling_support.install()
        super().__init__(*args, **kwargs)

    def setup_test_environment(self, **kwargs):
        key = "LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF"
        self._legacy_checkout_cutoff_previous = os.environ.get(key, _ENV_UNSET)
        os.environ[key] = TEST_LEGACY_NUMERIC_REFERENCE_CUTOFF
        self._password_hashers_override = override_settings(
            PASSWORD_HASHERS=TEST_PASSWORD_HASHERS
        )
        try:
            self._password_hashers_override.enable()
        except BaseException:
            # ``override_settings.enable()`` restores settings itself when a
            # setting-changed receiver fails, so do not disable it twice.
            self._password_hashers_override = None
            self._restore_legacy_checkout_cutoff_env()
            raise
        try:
            super().setup_test_environment(**kwargs)
        except BaseException:
            self._restore_test_environment_overrides()
            raise

    def teardown_test_environment(self, **kwargs):
        try:
            super().teardown_test_environment(**kwargs)
        finally:
            self._restore_test_environment_overrides()

    def _restore_test_environment_overrides(self):
        try:
            password_hashers_override = getattr(
                self, "_password_hashers_override", None
            )
            if password_hashers_override is not None:
                self._password_hashers_override = None
                password_hashers_override.disable()
        finally:
            self._restore_legacy_checkout_cutoff_env()

    def _restore_legacy_checkout_cutoff_env(self):
        key = "LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF"
        previous = self._legacy_checkout_cutoff_previous
        if previous is _ENV_UNSET:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous

    def build_suite(self, test_labels=None, **kwargs):
        suite = super().build_suite(test_labels=test_labels, **kwargs)
        shard = _read_shard_env()
        if shard is None:
            return suite
        index, count = shard

        # Flatten back to the individual TestCases. ``iter_test_cases`` yields
        # leaf TestCases regardless of whether ``suite`` is a flat TestSuite
        # (``--parallel 1``) or a ParallelTestSuite of subsuites
        # (``--parallel`` > 1), so reading ``test.id()`` is always safe here.
        all_cases = list(iter_test_cases(suite))
        kept_cases = [
            test
            for test in all_cases
            if _shard_for_test_id(test.id(), count) == index
        ]
        self.log(
            "Test sharding active: shard %d/%d selected %d of %d test(s)."
            % (index, count, len(kept_cases), len(all_cases))
        )

        # Rebuild a flat suite from just this shard's cases, then re-apply the
        # parent's parallel-wrapping so the shard parallelises like a normal
        # run. ``self.parallel`` is the *requested* worker count here; the
        # parent already lowered it once, so re-derive ``processes`` from the
        # shard's own case count (mirrors DiscoverRunner.build_suite).
        kept = self.test_suite(kept_cases)
        if self.parallel > 1:
            subsuites = partition_suite_by_case(kept)
            processes = min(self.parallel, len(subsuites))
            self.parallel = processes
            if processes > 1:
                kept = self.parallel_test_suite(
                    subsuites,
                    processes,
                    self.failfast,
                    self.debug_mode,
                    self.buffer,
                )
        return kept
