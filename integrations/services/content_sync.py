"""Site entry points for the package content sync engine (A2.3).

The package (``community_base.content_sync``) owns orchestration, locking,
checkout security, durable dispatch, and its own audit rows. This module is
the single site-side wrapper every legacy caller goes through:

- ``run_sync`` executes one source synchronously and merges the site parsers'
  rich per-item detail entries into the package ``SyncLog`` so Studio
  history keeps its ``{title, slug, action, content_type}`` shape;
- the legacy Django-Q task string ``integrations.services.github.sync_content_source``
  resolves here, so persisted tasks translate to the package engine;
- ``integrations.services.content_sync_queue`` queues through the package
  durable dispatcher.
"""

from community_base.content_sync.orchestration import (
    sync_content_source as package_sync_content_source,
)
from integrations.models import ContentSource


def run_sync(source, repo_dir=None, batch_id=None, force=False):
    """Run one content source through the package engine.

    Returns the package ``SyncLog`` row. Site parser detail entries are
    appended to ``items_detail`` after the package write so both the compact
    package entries and the legacy rich entries are available to operators.
    Rich per-file parser errors are prepended to ``log.errors`` in the legacy
    ``{'file', 'error'}`` shape next to the package's bounded entry.
    """
    force = force or _manifest_reconciliation_pending(source, repo_dir)

    if source.pk and not ContentSource.objects.filter(pk=source.pk).exists():
        # Issue #221: a persisted worker task can wake up after its source
        # row was deleted. Treat the sync as best-effort instead of raising.
        return None

    from content.sync_parsers import run_state

    collected_details = []
    collected_errors = []
    rich_families = set()
    extras = {}

    def _collect(family, details):
        collected_details.extend(details)

    def _collect_errors(family, errors):
        rich_families.add(family)
        collected_errors.extend(errors)

    def _collect_extras(run_extras):
        extras.update(run_extras)

    run_state.set_results_collector(_collect)
    run_state.set_errors_collector(_collect_errors)
    run_state.set_extras_collector(_collect_extras)
    try:
        log = package_sync_content_source(
            source,
            repo_dir=repo_dir,
            batch_id=batch_id,
            force=force,
        )
        if (
            not force
            and log.status == 'skipped'
            and not log.commit_sha
            and not log.warnings
        ):
            # The P6 mapping marks secretless sources disabled and the
            # package skips those outright. Legacy entry points always ran
            # the sync, so retry past the enabled gate. The head-unchanged
            # fast path is left intact: it records a warning and a commit.
            log = package_sync_content_source(
                source,
                repo_dir=repo_dir,
                batch_id=batch_id,
                force=True,
            )
    finally:
        run_state.set_results_collector(None)
        run_state.set_errors_collector(None)
        run_state.set_extras_collector(None)

    changed_fields = []
    if collected_errors:
        # Rich per-file entries replace the bounded package entry of the
        # same family so operator surfaces keep the legacy {'file', ...}
        # shape; families the site parsers did not enrich keep theirs.
        package_entries = [
            entry for entry in (log.errors or [])
            if not (
                isinstance(entry, dict)
                and entry.get('content_type') in rich_families
            )
        ]
        log.errors = collected_errors + package_entries
        changed_fields.append('errors')
    if collected_details:
        log.items_detail = list(log.items_detail or []) + collected_details
        changed_fields.append('items_detail')
    if extras:
        # Namespaced compatibility representation for the legacy tier
        # counters (package SyncLog has no dedicated columns).
        warnings = [w for w in (log.warnings or []) if not (
            isinstance(w, dict) and 'asl_compat' in w
        )]
        warnings.append({
            'asl_compat': {
                'tiers_synced': bool(extras.get('tiers_synced')),
                'tiers_count': int(extras.get('tiers_count', 0)),
            },
        })
        log.warnings = warnings
        changed_fields.append('warnings')
    if changed_fields:
        log.save(update_fields=changed_fields)
    return log


def sync_content_source(source, repo_dir=None, batch_id=None, force=False):
    """Legacy-compatible sync entry (old facade signature).

    Persisted Django-Q tasks reference
    ``integrations.services.github.sync_content_source``; that facade
    delegates here so existing queued tasks run the package engine.
    """
    return run_sync(
        source,
        repo_dir=repo_dir,
        batch_id=batch_id,
        force=force,
    )


def _manifest_reconciliation_pending(source, repo_dir):
    """Whether the head-unchanged fast path must not suppress this sync.

    A schema-first deployment can leave legacy Article manifests incomplete
    (for example when storage was unavailable during an earlier sync). The
    package fast path only consults the last commit and status, so the site
    escalates ``force`` for the next legacy-path sync until every article
    manifest is reconciled. ``repo_dir`` syncs never take the remote fast
    path anyway.
    """
    if repo_dir is not None:
        return False
    from content.models import Article

    return Article.objects.filter(
        source_repo=source.repo_name,
        image_manifest_complete=False,
    ).exists()
