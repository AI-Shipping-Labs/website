"""Tests for the deterministic test-sharding helpers in website.test_runner.

Issue #888: the Django suite is fanned out across a matrix of N shards in
CI so the wall-clock stays well under the job timeout as the suite grows.
The invariant the workflow relies on is that sharding is deterministic and
disjoint: every test id maps to exactly one shard, so summing the per-shard
``Ran N tests`` counts equals the single-job count (no test run twice, none
dropped). These tests assert that invariant on the assignment helper, which
is the load-bearing piece of the workflow change.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest import mock

from django.test.runner import ParallelTestSuite
from django.test.utils import iter_test_cases

from website.test_runner import (
    PicklableTracebackRunner,
    _read_shard_env,
    _shard_for_test_id,
)

# A representative spread of fully-qualified test ids.
SAMPLE_TEST_IDS = [
    f"app{a}.tests.test_mod{m}.Case{c}.test_method_{n}"
    for a in range(6)
    for m in range(5)
    for c in range(4)
    for n in range(10)
]


class ShardAssignmentTests(unittest.TestCase):
    def test_assignment_is_deterministic(self):
        # Same id + same N always returns the same shard, across calls.
        for test_id in SAMPLE_TEST_IDS[:50]:
            first = _shard_for_test_id(test_id, 4)
            for _ in range(5):
                self.assertEqual(_shard_for_test_id(test_id, 4), first)

    def test_shard_index_is_one_based_and_in_range(self):
        for count in (2, 4, 7):
            seen = {_shard_for_test_id(t, count) for t in SAMPLE_TEST_IDS}
            self.assertEqual(min(seen), 1)
            self.assertLessEqual(max(seen), count)
            self.assertTrue(seen.issubset(set(range(1, count + 1))))

    def test_partition_is_disjoint_and_complete(self):
        # Every id lands in exactly one shard; the union over all shards is
        # the full set with no duplicates and nothing dropped. This is the
        # exact property the CI determinism criterion depends on.
        for count in (1, 2, 4, 5, 8):
            buckets = {i: [] for i in range(1, count + 1)}
            for test_id in SAMPLE_TEST_IDS:
                buckets[_shard_for_test_id(test_id, max(count, 1))].append(test_id)
            recombined = [tid for i in range(1, count + 1) for tid in buckets[i]]
            self.assertEqual(
                sorted(recombined),
                sorted(SAMPLE_TEST_IDS),
                f"shard partition for N={count} dropped or duplicated tests",
            )
            self.assertEqual(
                sum(len(b) for b in buckets.values()),
                len(SAMPLE_TEST_IDS),
                f"summed shard counts != single-run count for N={count}",
            )

    def test_all_shards_get_some_tests(self):
        # SHA-256 spreads ids roughly uniformly; with ~1200 sample ids every
        # one of 4 shards should receive a non-trivial slice (guards against a
        # degenerate hash that funnels everything into one shard).
        count = 4
        buckets = {i: 0 for i in range(1, count + 1)}
        for test_id in SAMPLE_TEST_IDS:
            buckets[_shard_for_test_id(test_id, count)] += 1
        for i in range(1, count + 1):
            self.assertGreater(buckets[i], 0, f"shard {i} got no tests")


class BuildSuiteShardingTests(unittest.TestCase):
    """End-to-end ``build_suite`` sharding tests across the parallel modes.

    Regression guard for the bug where ``build_suite`` iterated the suite the
    parent returned and called ``test.id()`` on each element. Under
    ``--parallel`` > 1 the parent returns a ``ParallelTestSuite`` of nested
    ``TestSuite`` subsuites (which have no ``.id()``), so every CI shard
    crashed with ``AttributeError: 'TestSuite' object has no attribute 'id'``
    before running a single test. The prior unit tests only covered the
    assignment helper and the flat (``--parallel 1``) path, so the parallel
    crash slipped through. These tests run the real ``build_suite`` code path
    and assert the same disjoint/complete invariant under ``--parallel`` > 1.

    We discover the in-repo ``tests`` package as a real, fast target so the
    suite shapes (flat vs ``ParallelTestSuite``) are exercised exactly as in
    CI rather than mocked.
    """

    LABEL = "tests"

    def _ids_for_suite(self, suite):
        # ``iter_test_cases`` flattens both a plain TestSuite and a
        # ParallelTestSuite-of-subsuites down to leaf TestCases, so this works
        # regardless of the parallel mode.
        return sorted(test.id() for test in iter_test_cases(suite))

    def _build(self, *, parallel, shard=None):
        env = {}
        if shard is not None:
            index, count = shard
            env = {"TEST_SHARD_COUNT": str(count), "TEST_SHARD_INDEX": str(index)}
        with mock.patch.dict("os.environ", env):
            if shard is None:
                # Make sure no ambient shard env leaks in for the baseline.
                os.environ.pop("TEST_SHARD_COUNT", None)
                os.environ.pop("TEST_SHARD_INDEX", None)
            runner = PicklableTracebackRunner(parallel=parallel, verbosity=0)
            # build_suite may emit a "Found N test(s)" log to stderr; silence it.
            with contextlib.redirect_stderr(io.StringIO()):
                return runner.build_suite([self.LABEL])

    def test_unsharded_parallel_baseline(self):
        # Sanity: the discovery target has enough tests that --parallel 4
        # actually produces a ParallelTestSuite (otherwise the regression
        # below would not be exercised).
        suite = self._build(parallel=4)
        self.assertIsInstance(
            suite,
            ParallelTestSuite,
            "discovery target too small to wrap in a ParallelTestSuite; "
            "pick a label with more TestCases",
        )

    def test_shard_under_parallel_does_not_raise(self):
        # The exact crash repro: a shard built with parallel=4 must build a
        # runnable suite instead of raising AttributeError on TestSuite.id().
        suite = self._build(parallel=4, shard=(1, 4))
        self.assertGreater(suite.countTestCases(), 0)

    def test_parallel_shards_are_disjoint_and_complete(self):
        # Across all 4 shards under --parallel 4 the union of selected test
        # ids equals the full unsharded set, with no duplicates and nothing
        # dropped — same invariant the existing helper tests assert, but on
        # the real build_suite parallel path.
        full_ids = self._ids_for_suite(self._build(parallel=4))

        union = []
        for index in range(1, 5):
            shard_ids = self._ids_for_suite(self._build(parallel=4, shard=(index, 4)))
            union.extend(shard_ids)

        self.assertEqual(
            sorted(union),
            full_ids,
            "parallel shards dropped or duplicated tests vs the full run",
        )
        self.assertEqual(
            len(union),
            len(set(union)),
            "a test was assigned to more than one parallel shard",
        )
        self.assertEqual(
            sum(1 for _ in union),
            len(full_ids),
            "summed parallel shard counts != single-run count",
        )

    def test_parallel_and_serial_shards_select_same_tests(self):
        # The shard assignment must be identical whether the shard is built
        # with --parallel 1 (flat path) or --parallel 4 (wrapped path); only
        # the suite shape differs, not which tests run.
        for index in range(1, 5):
            serial = self._ids_for_suite(self._build(parallel=1, shard=(index, 4)))
            parallel = self._ids_for_suite(self._build(parallel=4, shard=(index, 4)))
            self.assertEqual(
                serial,
                parallel,
                f"shard {index}/4 selected different tests under parallel vs serial",
            )


class ReadShardEnvTests(unittest.TestCase):
    def _env(self, **overrides):
        return mock.patch.dict("os.environ", overrides, clear=True)

    def test_disabled_when_count_unset(self):
        with self._env():
            self.assertIsNone(_read_shard_env())

    def test_disabled_when_count_one(self):
        with self._env(TEST_SHARD_COUNT="1", TEST_SHARD_INDEX="1"):
            self.assertIsNone(_read_shard_env())

    def test_disabled_when_count_empty_string(self):
        with self._env(TEST_SHARD_COUNT="", TEST_SHARD_INDEX="1"):
            self.assertIsNone(_read_shard_env())

    def test_active_returns_index_and_count(self):
        with self._env(TEST_SHARD_COUNT="4", TEST_SHARD_INDEX="3"):
            self.assertEqual(_read_shard_env(), (3, 4))

    def test_missing_index_with_count_raises(self):
        with self._env(TEST_SHARD_COUNT="4"):
            with self.assertRaises(ValueError):
                _read_shard_env()

    def test_index_out_of_range_raises(self):
        with self._env(TEST_SHARD_COUNT="4", TEST_SHARD_INDEX="5"):
            with self.assertRaises(ValueError):
                _read_shard_env()
        with self._env(TEST_SHARD_COUNT="4", TEST_SHARD_INDEX="0"):
            with self.assertRaises(ValueError):
                _read_shard_env()
