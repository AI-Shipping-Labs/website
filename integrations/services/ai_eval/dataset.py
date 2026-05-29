"""Eval-dataset + gold-label loading for the AI-eval suite (issue #812).

A dataset fixture is the SAME shape #809 already parses
(``runner.build_onboarding_input`` / ``build_feedback_input``) PLUS an
eval-only ``meta`` sidecar that the callable adapter ignores. This module
reads that ``meta`` block (``id``, ``category``, ``phrasing``, ``source``,
optional ``expected``) and joins it to the per-assistant gold-label CSV by
``id``, so the callable input shape is never polluted with eval metadata.

The label CSV columns follow the buildcamp ``labels.csv`` shape: ``id``,
``correctness_label`` (``pass``/``fail``), ``failure_category``,
``split`` (``dev``/``test``), ``notes``. Rows with an empty
``correctness_label`` are unlabeled scaffold rows (a ``[HUMAN]`` step
fills them in); they carry their ``split`` but no gold label yet.
"""

import csv
from pathlib import Path

from integrations.services.ai_eval import runner

# Recognised eval metadata fields inside the ``meta`` sidecar.
META_FIELDS = ('id', 'category', 'phrasing', 'source', 'expected')

LABEL_COLUMNS = ('id', 'correctness_label', 'failure_category', 'split', 'notes')


class DatasetError(Exception):
    """A dataset fixture or label file could not be read/validated."""


def split_meta(data, *, source):
    """Split a loaded fixture dict into ``(callable_input, meta)``.

    ``meta`` is the eval sidecar (popped from a copy so the callable input
    never sees it). A fixture with no ``meta`` block raises
    :class:`DatasetError` -- every dataset scenario must carry at least an
    ``id`` so it can be joined to labels.
    """
    if 'meta' not in data:
        raise DatasetError(
            f'Dataset fixture {source} is missing the "meta" block '
            f'(needs at least an "id").'
        )
    meta = data['meta'] or {}
    if not isinstance(meta, dict) or not meta.get('id'):
        raise DatasetError(
            f'Dataset fixture {source} "meta" must be a mapping with an "id".'
        )
    callable_input = {k: v for k, v in data.items() if k != 'meta'}
    return callable_input, meta


def load_dataset(directory):
    """Load every fixture in ``directory`` into ``(callable_input, meta)``.

    Returns a list of dicts, each ``{'path', 'callable_input', 'meta'}``,
    sorted by filename for stable ordering. Raises :class:`DatasetError`
    naming the file on any parse/validation failure.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise DatasetError(f'Dataset directory not found: {directory}')
    fixtures = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in ('.json', '.yaml', '.yml')
    )
    if not fixtures:
        raise DatasetError(f'No dataset fixtures found in {directory}.')

    scenarios = []
    seen_ids = set()
    for path in fixtures:
        try:
            data = runner.load_fixture(path)
        except runner.FixtureError as exc:
            raise DatasetError(str(exc)) from None
        callable_input, meta = split_meta(data, source=str(path))
        scenario_id = meta['id']
        if scenario_id in seen_ids:
            raise DatasetError(
                f'Duplicate scenario id {scenario_id!r} (in {path.name}).'
            )
        seen_ids.add(scenario_id)
        scenarios.append({
            'path': path,
            'callable_input': callable_input,
            'meta': meta,
        })
    return scenarios


def load_labels(csv_path):
    """Load a gold-label CSV into ``{id: {column: value}}``.

    Unlabeled scaffold rows (empty ``correctness_label``) are kept -- they
    carry their ``split`` so the scaffold is complete before the human
    labels them. Raises :class:`DatasetError` on a missing file or a bad
    header.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise DatasetError(f'Label file not found: {csv_path}')
    with csv_path.open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in ('id', 'correctness_label', 'split') if c not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(
                f'Label file {csv_path} is missing columns: {", ".join(missing)}.'
            )
        labels = {}
        for row in reader:
            scenario_id = (row.get('id') or '').strip()
            if not scenario_id:
                continue
            labels[scenario_id] = {
                'correctness_label': (row.get('correctness_label') or '').strip(),
                'failure_category': (row.get('failure_category') or '').strip(),
                'split': (row.get('split') or 'dev').strip(),
                'notes': (row.get('notes') or '').strip(),
            }
    return labels


__all__ = [
    'DatasetError', 'META_FIELDS', 'LABEL_COLUMNS',
    'split_meta', 'load_dataset', 'load_labels',
]
