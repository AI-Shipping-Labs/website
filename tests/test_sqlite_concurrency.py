"""SQLite concurrency wiring + behaviour tests for issue #220.

Background. Multiple sync tasks running in parallel against SQLite (the
default dev DB) used to hit ``database is locked`` errors. SQLite supports
only one writer at a time; with ``Q_WORKERS>1`` and the default journal
mode + transaction mode, two writers race the same file lock and one of
them dies with ``OperationalError: database is locked``.

The fix lives in ``website/settings.py``: when ``ENGINE`` is sqlite3 we
populate ``OPTIONS`` with WAL + busy_timeout + synchronous=NORMAL via
``init_command`` and set ``transaction_mode='IMMEDIATE'``. Postgres
deployments are untouched (the branch only fires for sqlite3).

Three test classes:

1. ``SqliteOptionsWiringTest`` — settings-level assertions. Catches
   reverts of the OPTIONS dict or someone deleting ``transaction_mode``.
   No DB needed.

2. ``SqlitePragmasAppliedTest`` — live PRAGMA round-trip on the real
   connection: the OPTIONS values actually take effect.

3. ``SqliteConcurrentWritersTest`` — black-box proof against a real
   on-disk SQLite file. We can't reuse Django's test DB because it
   defaults to ``:memory:`` (memorydb shared cache), which has totally
   different locking semantics — ``journal_mode=WAL`` is meaningless on
   memory and busy_timeout effectively never triggers. The bug only
   reproduces against an on-disk file. So we open a temp ``*.sqlite3``,
   replicate the same OPTIONS via raw ``sqlite3.connect`` + the same
   PRAGMAs Django would run, then prove:

   - Without our PRAGMAs/transaction_mode, two concurrent writers
     reliably hit "database is locked".
   - With our PRAGMAs and an IMMEDIATE transaction mode, every writer
     succeeds.

   This is the test that would have caught the original bug — and the
   one that would catch a regression if someone removes the OPTIONS.
"""

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase


class SqliteOptionsWiringTest(SimpleTestCase):
    """Settings-level assertions for the SQLite concurrency tuning.

    These do not need a database; they fail fast if anyone deletes the
    ``OPTIONS`` config or reverts ``transaction_mode``.
    """

    def setUp(self):
        if 'sqlite' not in settings.DATABASES['default']['ENGINE']:
            self.skipTest('Sqlite-only wiring; Postgres deployments use MVCC.')
        self.options = settings.DATABASES['default'].get('OPTIONS', {})

    def test_init_command_enables_wal_journal_mode(self):
        init = self.options.get('init_command', '')
        self.assertIn(
            'journal_mode=WAL', init,
            'init_command must enable WAL so concurrent readers + one writer '
            'can coexist without raising "database is locked".',
        )

    def test_init_command_sets_busy_timeout(self):
        init = self.options.get('init_command', '')
        self.assertIn(
            'busy_timeout=30000', init,
            'init_command must set busy_timeout so writers wait for the lock '
            'instead of failing immediately.',
        )

    def test_init_command_sets_synchronous_normal(self):
        init = self.options.get('init_command', '')
        self.assertIn(
            'synchronous=NORMAL', init,
            "init_command should set synchronous=NORMAL — WAL's recommended "
            'fsync level. FULL is overkill for dev and noticeably slower.',
        )

    def test_transaction_mode_is_immediate(self):
        self.assertEqual(
            self.options.get('transaction_mode'), 'IMMEDIATE',
            'transaction_mode must be IMMEDIATE so write transactions '
            'acquire the write lock up front. Without it, two DEFERRED '
            'transactions can both start as readers and deadlock when '
            'they upgrade to writers — busy_timeout cannot recover from '
            'that case.',
        )


class SqlitePragmasAppliedTest(SimpleTestCase):
    """Live PRAGMA assertions: the OPTIONS actually take effect on the
    real connection. Catches the case where someone sets OPTIONS but a
    middleware / connection wrapper resets the PRAGMAs."""

    databases = {'default'}

    def setUp(self):
        if connection.vendor != 'sqlite':
            self.skipTest('Sqlite-only PRAGMA check.')

    def test_journal_mode_is_wal(self):
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode;')
            mode = cursor.fetchone()[0].lower()
        if mode == 'memory':
            # Django's default test DB is ``:memory:`` (or memorydb shared
            # cache), and SQLite reports ``journal_mode=memory`` for those
            # — WAL is meaningless without an on-disk file. Skip rather
            # than weaken the assertion: production / dev / CI all run
            # against the on-disk db.sqlite3 where this PRAGMA matters.
            self.skipTest('Test DB is in-memory; WAL only applies to on-disk SQLite.')
        self.assertEqual(
            mode, 'wal',
            f'PRAGMA journal_mode reports {mode!r}; init_command did not run.',
        )

    def test_busy_timeout_is_30s(self):
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA busy_timeout;')
            timeout_ms = cursor.fetchone()[0]
        self.assertEqual(
            timeout_ms, 30000,
            f'PRAGMA busy_timeout reports {timeout_ms} ms; init_command did not run.',
        )

    def test_transaction_mode_attribute_is_immediate(self):
        # Django stores the resolved transaction_mode on the wrapper.
        self.assertEqual(
            connection.transaction_mode, 'IMMEDIATE',
            'connection.transaction_mode should be IMMEDIATE; OPTIONS '
            'transaction_mode was not propagated.',
        )


def _run_writers(db_path, n_threads, *, apply_fix, hold_seconds=0.05):
    """Spawn ``n_threads`` raw sqlite3 writers against ``db_path``.

    Returns ``(errors, total_rows)``.

    - ``apply_fix=False`` reproduces the broken config: short busy_timeout,
      no WAL, default DEFERRED transaction mode. This is what surfaces
      "database is locked" under contention.
    - ``apply_fix=True`` mirrors the production fix: WAL (set once on
      the file by ``_fresh_db()`` before threads start, the same way
      Django's ``init_command`` runs at connection-open time) +
      per-connection busy_timeout=30000 + IMMEDIATE transaction mode.

    Each thread waits on a barrier so they all hit the write lock at the
    same instant. Whichever thread wins the race holds the write lock
    for ``hold_seconds`` before committing; without that hold, fast CI
    runners can let 8 writers serialise through the lock in under a
    millisecond and the negative control silently passes (see
    https://github.com/AI-Shipping-Labs/website/issues/220 CI failure on
    commit a7ac69a). The fix path uses a 30 s busy_timeout + IMMEDIATE,
    so a 50 ms hold is invisible to it; the broken path uses a 10 ms
    busy_timeout, so the hold guarantees at least one losing writer
    exceeds the timeout and surfaces SQLITE_BUSY.
    """
    errors: list[Exception] = []
    errors_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def writer(i):
        if apply_fix:
            con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
            # Mirror website/settings.py init_command — except for
            # journal_mode=WAL, which the harness sets once in
            # ``_fresh_db()`` BEFORE any threads start. Setting WAL mode
            # is itself a write op that needs the write lock; if every
            # thread tries to set it concurrently before busy_timeout is
            # in effect, the losers get SQLITE_BUSY immediately. Django
            # avoids this in production because ``init_command`` runs at
            # connection-open time, well before user-level contention,
            # and WAL is sticky on the file. We replicate that here by
            # making _fresh_db() the WAL-setter.
            con.execute('PRAGMA busy_timeout=30000')
            con.execute('PRAGMA synchronous=NORMAL')
        else:
            # Tight busy_timeout exposes the lock race in milliseconds
            # instead of letting SQLite's default 5 s busy handler
            # silently absorb the contention. We set it via PRAGMA (not
            # the connect-level ``timeout`` kwarg) because the connect
            # kwarg is the same busy_timeout under the hood and we want
            # an explicit, surgical value here. 10 ms is well below the
            # 50 ms hold below, so any losing writer is guaranteed to
            # exhaust busy_timeout and raise SQLITE_BUSY.
            con = sqlite3.connect(db_path, timeout=0, isolation_level=None)
            con.execute('PRAGMA busy_timeout=10')
        try:
            barrier.wait()
            # IMMEDIATE matches Django's transaction_mode='IMMEDIATE';
            # DEFERRED is the broken default that lets two readers
            # deadlock on upgrade.
            begin = 'BEGIN IMMEDIATE' if apply_fix else 'BEGIN DEFERRED'
            con.execute(begin)
            con.execute('INSERT INTO concurrency_test (val) VALUES (?)', (f'thread-{i}',))
            # Hold the write lock long enough that at least one losing
            # writer exhausts its busy_timeout. Skipped when there is
            # only one writer (no contention possible anyway).
            if hold_seconds and n_threads > 1:
                time.sleep(hold_seconds)
            con.execute('COMMIT')
        except sqlite3.OperationalError as exc:
            with errors_lock:
                errors.append(exc)
        finally:
            con.close()

    # daemon=True ensures the python interpreter can exit even if a writer
    # thread is wedged inside sqlite C code (e.g. blocked in ``sqlite3_step``
    # waiting on the write lock). ``Thread.join(timeout=60)`` returns control
    # after the timeout, but a non-daemon thread that is still alive in C
    # code keeps the process alive at interpreter shutdown — manifesting in
    # CI as a multi-hour hang after the test method already returned. See
    # https://github.com/AI-Shipping-Labs/website/issues/270.
    threads = [
        threading.Thread(target=writer, args=(i,), daemon=True)
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # Count rows that actually committed.
    audit = sqlite3.connect(db_path)
    try:
        total_rows = audit.execute('SELECT COUNT(*) FROM concurrency_test').fetchone()[0]
    finally:
        audit.close()
    return errors, total_rows


class SqliteConcurrentWritersTest(SimpleTestCase):
    """Black-box concurrency test against a real on-disk SQLite file.

    Two writers race the write lock under both the broken and the fixed
    configs. The fixed config must let every writer succeed; the broken
    config is asserted to fail (so the test would catch a regression
    that silently makes our fix a no-op).
    """

    def setUp(self):
        if connection.vendor != 'sqlite':
            self.skipTest(
                'Concurrency test targets the SQLite write-lock race; '
                'Postgres uses MVCC and does not exhibit this bug.',
            )

    def _fresh_db(self):
        """Create a temp on-disk SQLite with a single test table.

        We set ``journal_mode=WAL`` here, before any test threads run,
        because flipping a database into WAL mode is itself a write op
        that requires the write lock. If we instead let each writer
        thread set WAL on its own connection at the same instant, the
        thread that grabs the write lock first wins; the rest see
        SQLITE_BUSY immediately because their per-connection
        ``busy_timeout`` hasn't been set yet (it's the next PRAGMA in
        line). Django's production config doesn't hit this because
        ``init_command`` runs once when the connection opens, long
        before user-level contention. The harness mirrors that: WAL is
        a file-level mode, so setting it once on a fresh DB sticks for
        every later connection.
        """
        tmpdir = tempfile.mkdtemp(prefix='sqlite-concurrency-')
        db_path = str(Path(tmpdir) / 'test.sqlite3')
        con = sqlite3.connect(db_path, isolation_level=None)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute(
            'CREATE TABLE concurrency_test '
            '(id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT NOT NULL)',
        )
        con.close()
        return db_path

    def test_without_fix_concurrent_writers_hit_database_is_locked(self):
        """Negative control: prove the bug exists without our fix.

        If this test ever stops failing-without-fix, the
        ``test_with_fix_*`` test below loses its meaning — somebody has
        either changed SQLite's behaviour or weakened the harness so it
        no longer reproduces real contention.
        """
        db_path = self._fresh_db()
        errors, total_rows = _run_writers(db_path, n_threads=8, apply_fix=False)
        self.assertGreater(
            len(errors), 0,
            'Negative control failed: 8 concurrent writers with the broken '
            "config should produce at least one 'database is locked' error. "
            f'Got 0 errors, {total_rows} rows. The harness no longer '
            'reproduces real contention — fix it before trusting the '
            'positive test.',
        )
        self.assertTrue(
            any('database is locked' in str(e) or 'locked' in str(e) for e in errors),
            f'Expected "database is locked" in errors, got: {[str(e) for e in errors]}',
        )

    def test_with_fix_concurrent_writers_all_succeed(self):
        """Positive case: WAL + busy_timeout + IMMEDIATE lets every
        writer through under the same contention pattern as the
        negative control (n=8).

        The earlier flake on this test (issue #268) was misdiagnosed as
        contention scaling: the fix had each writer thread run ``PRAGMA
        journal_mode=WAL`` on its own connection. That PRAGMA is itself
        a write op needing the write lock, and it runs *before* the
        next PRAGMA (``busy_timeout=30000``) takes effect — so losing
        threads got SQLITE_BUSY immediately, with zero rows committed.
        ``_fresh_db()`` now sets WAL once before any thread starts,
        mirroring how Django's ``init_command`` runs WAL at connection
        open. With that fix, all 8 writers succeed reliably.
        """
        db_path = self._fresh_db()
        n_threads = 8
        errors, total_rows = _run_writers(db_path, n_threads=n_threads, apply_fix=True)
        first_error = errors[0] if errors else None
        self.assertEqual(
            errors, [],
            f'{len(errors)} concurrent writer(s) failed with the fix in place; '
            f'first: {first_error!r}. Expected WAL + busy_timeout=30s + '
            'BEGIN IMMEDIATE to serialise writers without raising.',
        )
        self.assertEqual(
            total_rows, n_threads,
            f'Expected {n_threads} committed rows, got {total_rows}. '
            'Some writer succeeded silently without committing.',
        )


class SqliteSettingsBranchPostgresTest(SimpleTestCase):
    """Document the contract: the SQLite OPTIONS block must be guarded
    by an ``ENGINE == sqlite3`` check, so Postgres deployments aren't
    fed PRAGMAs they don't understand.

    We can't easily switch ENGINE at test time (Django wires connections
    at startup), but we can assert the guard exists in source — a
    cheap belt-and-suspenders against someone removing the branch.
    """

    def test_settings_branches_on_sqlite_engine(self):
        settings_path = Path(settings.BASE_DIR) / 'website' / 'settings.py'
        source = settings_path.read_text()
        self.assertIn(
            "DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'",
            source,
            'SQLite OPTIONS block must be guarded by an ENGINE check so '
            'Postgres deploys do not receive PRAGMA-style init_commands '
            'they cannot parse.',
        )
