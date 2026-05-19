"""Smoke-test task for verifying the worker is consuming jobs.

Issue #697: an operator clicks "Test worker" on /studio/worker/ to confirm
that a freshly deployed worker is actually picking up jobs from the queue.
The task logs the hostname + PID of the worker that ran it so the operator
can tell which worker handled it.
"""

import logging
import os
import socket

logger = logging.getLogger(__name__)


def run():
    """Log a hello-world line tagged with the worker's hostname + PID.

    Returns the same string so it shows up in the Task.result column on the
    worker dashboard.
    """
    message = (
        f'hello world from worker on {socket.gethostname()} pid={os.getpid()}'
    )
    logger.info(
        'hello world from worker on %s pid=%s', socket.gethostname(), os.getpid(),
    )
    return message
