"""Consolidate ContentSource rows: drop content_type/content_path; one row per repo.

Issue #310. The platform used to spread each repo across many ``ContentSource``
rows — one per ``(repo_name, content_type, content_path)`` triple. The new
sync walker dispatches per file by inspecting filename + frontmatter +
location, so per-type rows no longer earn their keep.

This migration:

1. Picks one canonical ``ContentSource`` row per ``repo_name`` (preferring
   the most-recent successful sync's ``last_synced_commit``).
2. Repoints every ``SyncLog.source_id`` to the canonical row BEFORE deleting
   the non-canonical rows. CASCADE deletes would otherwise eat the history.
3. For each non-canonical row, prepends its ``content_path`` to the
   ``source_path`` of every content row (Article, Course, Module, Unit,
   Project, Download, CuratedLink, Event, Workshop, WorkshopPage,
   InterviewCategory, Instructor) it sourced — so ``get_github_edit_url``
   keeps producing correct URLs after the per-source ``content_path``
   prefix is gone.
4. Deletes non-canonical rows.
5. Drops the ``unique_together`` constraint, then drops the
   ``content_type`` and ``content_path`` columns, and adds ``unique=True``
   to ``repo_name``.

Reverse: the schema operations are reversible (Django re-adds the columns
with their defaults), but the data step is a no-op — the per-type splits
that existed before forward migration cannot be reconstructed from the
consolidated row, so reversing leaves ``content_type`` and ``content_path``
empty / NULL on every row. Treat reversal as "schema rollback only, accept
information loss". The migration is provided as a noop reverse rather than
raising so test fixtures (and dev workflows) can round-trip the schema.
"""

from django.db import migrations, models

# Models that carry ``source_repo`` + ``source_path`` (i.e. were synced
# from a ``ContentSource``). Each tuple is ``(app_label, ModelName)``. We
# rewrite ``source_path`` for these rows to make sure the repo-relative
# path includes the old ``content_path`` prefix once the column is gone.
_SYNCED_MODELS = [
    ('content', 'Article'),
    ('content', 'Course'),
    ('content', 'Module'),
    ('content', 'Unit'),
    ('content', 'Project'),
    ('content', 'Download'),
    ('content', 'CuratedLink'),
    ('content', 'InterviewCategory'),
    ('content', 'Workshop'),
    ('content', 'WorkshopPage'),
    ('content', 'Instructor'),
    ('events', 'Event'),
]


def _consolidate_forwards(apps, schema_editor):
    ContentSource = apps.get_model('integrations', 'ContentSource')
    SyncLog = apps.get_model('integrations', 'SyncLog')

    # Group rows by repo_name.
    by_repo = {}
    for source in ContentSource.objects.all().order_by('created_at'):
        by_repo.setdefault(source.repo_name, []).append(source)

    for repo_name, rows in by_repo.items():
        if len(rows) == 1:
            # Already one row — nothing to consolidate, but still rewrite
            # source_paths so legacy rows that stored content-path-relative
            # source_paths get the prefix applied uniformly.
            canonical = rows[0]
            content_path = (canonical.content_path or '').strip('/')
            if content_path:
                _rewrite_source_paths(
                    apps, repo_name, canonical.content_type, content_path,
                )
            continue

        # Pick canonical: prefer the most recent successful sync, fall back
        # to oldest by created_at.
        successful = [
            r for r in rows
            if r.last_sync_status == 'success' and r.last_synced_at
        ]
        if successful:
            canonical = max(successful, key=lambda r: r.last_synced_at)
        else:
            canonical = rows[0]

        non_canonical = [r for r in rows if r.pk != canonical.pk]
        non_canonical_ids = [r.pk for r in non_canonical]

        # Step 1: repoint every SyncLog.source_id from non-canonical rows
        # to the canonical row, preserving the historical record. Must
        # happen BEFORE the delete or CASCADE would drop the SyncLogs.
        if non_canonical_ids:
            SyncLog.objects.filter(source_id__in=non_canonical_ids).update(
                source=canonical,
            )

        # Step 2: rewrite source_path on every synced model row that came
        # from one of the non-canonical (repo, content_type, content_path)
        # combinations. Prepends the old content_path so the resulting
        # source_path is repo-relative. The canonical row's content_path is
        # also applied so all rows for this repo end up uniformly prefixed.
        for row in rows:
            content_path = (row.content_path or '').strip('/')
            if not content_path:
                continue
            _rewrite_source_paths(
                apps, repo_name, row.content_type, content_path,
            )

        # Step 3: delete the non-canonical rows.
        if non_canonical_ids:
            ContentSource.objects.filter(pk__in=non_canonical_ids).delete()


def _rewrite_source_paths(apps, repo_name, content_type, content_path):
    """Prepend ``content_path/`` to ``source_path`` on every synced row that
    came from this ``(repo_name, content_type)`` source.

    Idempotent: if a row already starts with ``content_path/`` we leave it
    alone. This handles the case where some legacy rows were written with
    the prefix already baked in (different code paths over time).
    """
    if not content_path:
        return

    prefix = f'{content_path}/'

    # Type-to-models map. Multiple model names can share one content_type
    # (e.g. ``course`` covers Course/Module/Unit). The migration is liberal
    # here — rewriting a row that already had the prefix is a no-op, and
    # rewriting a row that came from a different content_type but matches
    # the same prefix is fine because content_paths within one repo are
    # disjoint by convention (``blog/``, ``courses/``, ``projects/``...).
    type_to_apps_models = {
        'article': [('content', 'Article')],
        'course': [
            ('content', 'Course'),
            ('content', 'Module'),
            ('content', 'Unit'),
        ],
        'project': [('content', 'Project')],
        'resource': [
            ('content', 'Download'),
            ('content', 'CuratedLink'),
        ],
        'event': [('events', 'Event')],
        'interview_question': [('content', 'InterviewCategory')],
        'instructor': [('content', 'Instructor')],
        'workshop': [
            ('content', 'Workshop'),
            ('content', 'WorkshopPage'),
        ],
    }

    for app_label, model_name in type_to_apps_models.get(content_type, []):
        try:
            Model = apps.get_model(app_label, model_name)
        except LookupError:
            continue

        rows = Model.objects.filter(source_repo=repo_name).only(
            'pk', 'source_path',
        )
        for row in rows:
            sp = row.source_path or ''
            if not sp:
                continue
            if sp.startswith(prefix):
                continue
            new_sp = f'{prefix}{sp}'
            Model.objects.filter(pk=row.pk).update(source_path=new_sp)


def _consolidate_reverse(apps, schema_editor):
    """Data-step reverse: no-op.

    The forward migration deletes non-canonical rows and rewrites
    ``source_path`` on synced content rows. We cannot reconstruct the
    per-type rows from a consolidated row. The schema operations re-add
    ``content_type`` and ``content_path`` columns; this hook intentionally
    does nothing so the schema rollback succeeds even though the original
    data shape is gone.
    """
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0020_alter_contentsource_unique_together'),
    ]

    operations = [
        # Phase 1: data step. Consolidate rows + repoint SyncLog FKs +
        # rewrite source_paths. Run BEFORE the schema change so the
        # ORM still has access to ``content_type`` and ``content_path``.
        migrations.RunPython(_consolidate_forwards, _consolidate_reverse),

        # Phase 2: schema. Drop the unique_together first (referenced columns
        # must exist when the constraint is altered), then drop the columns,
        # then add unique=True to repo_name.
        migrations.AlterUniqueTogether(
            name='contentsource',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='contentsource',
            name='content_type',
        ),
        migrations.RemoveField(
            model_name='contentsource',
            name='content_path',
        ),
        migrations.AlterField(
            model_name='contentsource',
            name='repo_name',
            field=models.CharField(
                help_text='Full GitHub repo name (e.g. AI-Shipping-Labs/content).',
                max_length=300,
                unique=True,
            ),
        ),
    ]
