"""Generic glue between moved site dispatchers and the package parser protocol.

The package engine calls, per registered parser:

- ``discover(checkout, source)`` -> ``SourceItem`` values (duplicate keys raise);
- ``upsert(item, source, media)`` -> object or ``UpsertResult`` (one action
  per item is recorded);
- ``soft_delete_missing(seen_keys, source)`` -> deleted objects or a count.

Site dispatchers keep their historical per-file behavior: a broken file is a
bounded error while already-processed good files stay synced and other parser
families continue. Per-file exceptions are therefore captured into the family
error sink instead of aborting the run; once the family finished (including
stale cleanup) the accumulated errors are raised once as
:class:`FamilyPartialError`, which the package orchestration turns into a
single bounded error entry and a ``partial`` sync status.
"""

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content.sync_parsers import run_state
from content.sync_parsers.checkout_view import ContentCheckoutError


class FamilyPartialError(Exception):
    """Aggregated bounded error for one parser family."""

    def __init__(self, content_type, errors):
        self.content_type = content_type
        self.errors = errors
        summary = '; '.join(
            f"{error.get('file') or ''}: {error.get('error')}" for error in errors[:3]
        )
        more = '' if len(errors) <= 3 else f' (+{len(errors) - 3} more)'
        super().__init__(f'{len(errors)} file error(s): {summary}{more}')


class FamilyParser:
    """Adapter implementing the package parser protocol for one family.

    Subclasses provide:

    - ``content_type``: package registration key;
    - ``iter_items(run)``: yield ``(key, payload)`` pairs; parse failures must
      be recorded in the family error sink and skipped;
    - ``process(run, payload)``: run one item through the moved dispatcher
      body with a per-item legacy stats dict; return ``(action, obj)``;
    - ``cleanup(run)``: moved stale-content tail; returns deleted count.
    """

    content_type = ''
    state_name = ''

    # -- package protocol ----------------------------------------------------

    def discover(self, checkout, source):
        run = run_state.run_for(checkout, source)
        with run_state.activate(run):
            state = self._state(run)
            # Legacy pipeline order: the S3 media pre-pass ran before any
            # dispatcher; the first parser's discover preserves that.
            run.ensure_media_prepass()
            # Classifier parse errors surface as the first family's
            # bounded error, like the legacy stats['errors'] path.
            state.errors.extend(run.drain_classify_errors())
            items = []
            try:
                for key, payload in self.iter_items(run):
                    items.append(SourceItem(key=key, path=payload.get('rel_path', ''), data=payload))
            except ContentCheckoutError as error:
                # Boundary refusals leave the package as a bounded family
                # error; publish the rich legacy-shaped entry first so
                # operator surfaces keep the {'file': ...} contract.
                run_state.emit_errors(self.content_type, [error.as_error()])
                raise
            return items

    def upsert(self, item, source, media):
        run = run_state.current_run()
        if run is None:
            raise RuntimeError('No active sync run; upsert requires discover first')
        with run_state.activate(run):
            state = self._state(run)
            payload = dict(item.data)
            try:
                action, obj = self.process(run, payload)
            except ContentCheckoutError as error:
                run_state.emit_errors(self.content_type, [error.as_error()])
                raise
            except Exception as error:  # noqa: BLE001 - bounded per-file capture
                from content.sync_parsers.checkout_view import raise_if_checkout_error

                raise_if_checkout_error(error)
                state.record_error({
                    'file': payload.get('rel_path', ''),
                    'error': str(error),
                })
                state.failed.add(payload.get('identity') or payload.get('rel_path', ''))
                return UpsertResult(None, 'unchanged')
            return UpsertResult(obj, action)

    def soft_delete_missing(self, seen_keys, source):
        run = run_state.current_run()
        if run is None:
            raise RuntimeError('No active sync run; soft delete requires discover first')
        with run_state.activate(run):
            state = self._state(run)
            deleted = self.cleanup(run) or 0
            errors = list(state.errors)
            media_errors = self._drain_media_errors(run)
            if media_errors:
                errors = media_errors + errors
            state.errors.clear()
            run_state.emit_results(self.content_type, state.details)
            state.details.clear()
            if errors:
                run_state.emit_errors(self.content_type, errors)
                raise FamilyPartialError(self.content_type, errors)
            return deleted

    # -- hooks ----------------------------------------------------------------

    def _state(self, run):
        return run.family(self.state_name or self.content_type)

    def _drain_media_errors(self, run):
        """First family to finish reports the media pre-pass errors."""
        stats = run.media_stats()
        if stats is None:
            return []
        run.record_media_stats(None)
        return list(stats.get('errors') or [])

    def item_stats(self):
        """Fresh legacy-shaped stats dict for one dispatcher body call."""
        stats = {
            'created': 0,
            'updated': 0,
            'unchanged': 0,
            'deleted': 0,
            'errors': [],
            'items_detail': [],
        }
        return stats

    def absorb(self, run, stats):
        """Merge a per-item legacy stats dict into the family sink."""
        state = self._state(run)
        state.errors.extend(stats.get('errors') or [])
        state.details.extend(stats.get('items_detail') or [])
        if stats.get('created'):
            return 'created'
        if stats.get('updated'):
            return 'updated'
        if stats.get('deleted'):
            return 'deleted'
        return 'unchanged'

    # -- required overrides ----------------------------------------------------

    def iter_items(self, run):
        raise NotImplementedError

    def process(self, run, payload):
        raise NotImplementedError

    def cleanup(self, run):
        raise NotImplementedError


def register(parser_cls):
    """Register one family parser with the package registry."""
    register_parser(parser_cls.content_type, parser_cls())
