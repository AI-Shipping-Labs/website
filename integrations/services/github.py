"""Compatibility facade for GitHub content sync (A2.3).

The synchronization engine lives in the released
``community_base.content_sync`` package; site parsers live in
``content.sync_parsers``. This module keeps the historical import paths and
the persisted Django-Q task string
``integrations.services.github.sync_content_source`` working while callers
and queued tasks translate to the package engine. No new feature code may
import from here (only the task string and legacy tests do).
"""

from integrations.services.content_sync import sync_content_source

__all__ = ['sync_content_source']
