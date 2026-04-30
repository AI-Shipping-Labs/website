"""Shared lifecycle helpers for GitHub content sync dispatchers."""

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from django.db import models

from integrations.services.github_sync.parsing import _defaults_differ

ModelT = TypeVar('ModelT', bound=models.Model)
Stats = MutableMapping[str, Any]
DetailBuilder = Callable[[ModelT, str], Mapping[str, Any]]


@dataclass(frozen=True)
class SyncLifecycleResult:
    """Outcome returned by ``upsert_synced_object``."""

    instance: models.Model
    created: bool
    changed: bool


def find_synced_object(
    lookups: Iterable[Callable[[], ModelT | None]],
) -> ModelT | None:
    """Return the first object found by caller-provided lookup policy."""
    for lookup in lookups:
        obj = lookup()
        if obj is not None:
            return obj
    return None


def record_sync_action(
    stats: Stats,
    *,
    action: str,
    detail: Mapping[str, Any] | None = None,
    count: int = 1,
) -> None:
    """Record a lifecycle counter and optional per-item detail entry."""
    stats[action] += count
    if detail is not None:
        stats['items_detail'].append(dict(detail))


def apply_defaults_if_changed(
    instance: ModelT,
    defaults: Mapping[str, Any],
    *,
    changed: bool = False,
    apply_identity: Callable[[ModelT], None] | None = None,
) -> bool:
    """Apply defaults and save only when identity or synced fields differ."""
    should_save = changed or _defaults_differ(instance, defaults)
    if not should_save:
        return False

    if apply_identity is not None:
        apply_identity(instance)
    for key, value in defaults.items():
        setattr(instance, key, value)
    instance.save()
    return True


def upsert_synced_object(
    *,
    model: type[ModelT],
    lookup: Callable[[], ModelT | None],
    defaults: Mapping[str, Any],
    stats: Stats,
    detail: DetailBuilder[ModelT],
    create_kwargs: Mapping[str, Any] | None = None,
    identity_changed: Callable[[ModelT], bool] | None = None,
    apply_identity: Callable[[ModelT], None] | None = None,
) -> SyncLifecycleResult:
    """Create/update one synced object and record lifecycle stats.

    Callers supply lookup and identity behavior so model-specific matching
    policies stay in the dispatcher.
    """
    obj = lookup()
    if obj is None:
        obj = model(**(create_kwargs or {}), **defaults)
        obj.save()
        record_sync_action(
            stats, action='created', detail=detail(obj, 'created'),
        )
        return SyncLifecycleResult(instance=obj, created=True, changed=True)

    if apply_defaults_if_changed(
        obj,
        defaults,
        changed=identity_changed(obj) if identity_changed else False,
        apply_identity=apply_identity,
    ):
        record_sync_action(
            stats, action='updated', detail=detail(obj, 'updated'),
        )
        return SyncLifecycleResult(instance=obj, created=False, changed=True)

    record_sync_action(stats, action='unchanged')
    return SyncLifecycleResult(instance=obj, created=False, changed=False)


def cleanup_stale_synced_objects(
    queryset,
    *,
    stats: Stats,
    detail: DetailBuilder[ModelT],
    cleanup: Callable[[Sequence[ModelT]], None],
) -> int:
    """Record deleted details and run caller-provided stale cleanup behavior."""
    stale_objects = list(queryset)
    if not stale_objects:
        return 0

    for obj in stale_objects:
        stats['items_detail'].append(dict(detail(obj, 'deleted')))
    cleanup(stale_objects)
    stats['deleted'] += len(stale_objects)
    return len(stale_objects)
