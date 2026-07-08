"""Gunicorn hooks for the website container."""

from integrations.services.observability import init_logfire_once


def post_fork(server, worker):
    """Initialize observability after gunicorn forks a serving worker.

    ``AppConfig.ready()`` runs during ``django.setup()`` on the pre-bind path.
    Deferring Logfire here keeps slow imports/network setup out of ECS health
    startup while still configuring each serving worker before it handles
    requests.
    """
    return init_logfire_once()
