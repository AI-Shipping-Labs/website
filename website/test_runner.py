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

import tblib.pickling_support
from django.test.runner import DiscoverRunner

tblib.pickling_support.install()


class PicklableTracebackRunner(DiscoverRunner):
    """``DiscoverRunner`` with tblib pickling support installed.

    Installing tblib at module import time covers the case where a test
    is collected and run before this class is instantiated. We also
    re-install in ``__init__`` defensively in case the parent module
    was imported before tblib was available (e.g. in a partial install).
    """

    def __init__(self, *args, **kwargs):
        tblib.pickling_support.install()
        super().__init__(*args, **kwargs)
