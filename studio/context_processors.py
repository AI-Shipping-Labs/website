"""Studio template context processors.

Adds variables that should be available on every studio page.
"""

from studio.worker_health import get_worker_status


def worker_status_banner(request):
    """Expose worker liveness for the studio-wide banner.

    Only runs for ``/studio/...`` requests — public pages don't need the banner.
    Returns ``{'studio_worker_status': None}`` for non-studio requests so the
    banner template short-circuits cleanly.
    """
    path = request.path or ''
    if not path.startswith('/studio/'):
        return {'studio_worker_status': None}

    return {'studio_worker_status': get_worker_status()}
