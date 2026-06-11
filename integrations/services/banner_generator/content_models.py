"""Canonical content-type -> model mapping for the banner pipeline.

A single source of truth for the ``content_type`` slug -> ``Model`` lookup
used by both the dispatcher (:mod:`integrations.services.banner_generator.dispatch`)
and the async render task (:mod:`integrations.services.banner_generator.tasks`).

This module is intentionally import-light: it pulls in no
``jobs.tasks.helpers`` (the dispatcher's dependency) and no Django models at
import time. The mapping is declared as ``slug -> dotted-path`` strings, and
the classes are imported lazily inside :func:`model_for` so the worker task
can use the mapping without importing the dispatcher module — preserving the
deferred-import decoupling from #788.

``SUPPORTED_CONTENT_TYPES`` is derived from ``CONTENT_TYPE_MODELS`` so the
tuple of known slugs can never drift from the mapping.
"""

from importlib import import_module

# Canonical content-type slug -> dotted model path. The values are dotted
# strings (not classes) so this module imports no Django models at module
# load — the deferred-import pattern that lets the worker task consume this
# without dragging in the dispatcher's ``jobs.tasks.helpers`` dependency.
CONTENT_TYPE_MODELS = {
    'article': 'content.models.Article',
    'course': 'content.models.Course',
    'project': 'content.models.Project',
    'download': 'content.models.Download',
    'workshop': 'content.models.Workshop',
    'event': 'events.models.Event',
    'event_series': 'events.models.EventSeries',
}

# Slugs the banner pipeline knows how to render. Derived from the canonical
# map so it can never drift from it.
SUPPORTED_CONTENT_TYPES = tuple(CONTENT_TYPE_MODELS)


def model_for(content_type):
    """Resolve a ``content_type`` slug to its model class, or ``None``."""
    dotted_path = CONTENT_TYPE_MODELS.get(content_type)
    if dotted_path is None:
        return None
    module_path, _, class_name = dotted_path.rpartition('.')
    return getattr(import_module(module_path), class_name)
