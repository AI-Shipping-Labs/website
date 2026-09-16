"""Curriculum nesting: Module.parent/is_bonus/available_after_days,
Unit.kind/session_position/is_bonus (issue #1674).

Purely additive for the schema: ``Module.parent``/``available_after_days``
and ``Unit.session_position`` are nullable with no default needed (NULL is
the correct DB default for a new nullable column). ``Module.is_bonus`` /
``Unit.is_bonus`` ship with ``db_default=False`` and ``Unit.kind`` ships
with ``db_default='lesson'`` so INSERTs from an old container mid-rolling
-deploy (which doesn't know the columns exist) still satisfy the NOT NULL
constraints.

Bundled in this same migration (owner decision, not the general "no data
migration bundled into the schema migration" guidance that applies to
every other field here): a data migration that relabels existing
homework-bearing units to ``kind='homework'``. See
``_backfill_homework_kind`` below for the exact predicate and reporting.

``Cohort.mode`` is a separate migration (0065) — different model, no
ordering dependency on this one.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def _backfill_homework_kind(apps, schema_editor):
    """Relabel existing homework-only units to ``kind='homework'``.

    Predicate, read directly from how the sync parser populates these
    fields (``content/sync_parsers/families/courses.py:_sync_module_units``):
    ``is_homework: true`` in unit frontmatter routes the parsed body into
    ``Unit.homework`` instead of ``Unit.body`` — mutually exclusive BY SYNC
    DESIGN. So the honest predicate is "non-empty homework, empty body",
    not a title/slug guess.

    Rows with BOTH non-empty ``homework`` AND non-empty ``body`` (only
    possible via Studio's local ``unit_edit``, which sets the two fields
    independently for non-synced courses) are NOT relabelled — ``kind``
    is one enum value per unit and cannot express "both a lesson body and
    homework text". They stay ``kind='lesson'`` (the default) and are
    printed for manual review, same visible-in-``migrate``-stdout pattern
    as ``content/migrations/0032_backfill_instructors.py``.

    Batched (chunked by primary-key range, 500 rows at a time) rather than
    one unbounded UPDATE. Idempotent: the predicate excludes rows already
    at ``kind='homework'``, so a re-run is a clean no-op.
    """
    Unit = apps.get_model('content', 'Unit')

    homework_only_ids = list(
        Unit.objects
        .exclude(homework='')
        .exclude(homework__isnull=True)
        .filter(Q(body='') | Q(body__isnull=True))
        .exclude(kind='homework')
        .order_by('pk')
        .values_list('pk', flat=True)
    )

    relabelled = 0
    chunk_size = 500
    for start in range(0, len(homework_only_ids), chunk_size):
        chunk = homework_only_ids[start:start + chunk_size]
        relabelled += Unit.objects.filter(pk__in=chunk).update(kind='homework')

    manual_review = list(
        Unit.objects
        .exclude(homework='')
        .exclude(homework__isnull=True)
        .exclude(body='')
        .exclude(body__isnull=True)
        .select_related('module', 'module__course')
        .order_by('pk')
    )

    print(f'[0064_curriculum_nesting] relabelled {relabelled} unit(s) to kind="homework".')
    if manual_review:
        print(
            f'[0064_curriculum_nesting] {len(manual_review)} unit(s) have '
            'BOTH non-empty homework and body text — left at kind="lesson" '
            'for manual review (relabelling would drop the body from the '
            "reader's homework rendering path):"
        )
        for unit in manual_review:
            course_slug = unit.module.course.slug if unit.module_id else '?'
            print(
                f'  - Unit id={unit.pk} slug={unit.slug!r} '
                f'course={course_slug!r} module={unit.module.slug!r}'
            )
    else:
        print('[0064_curriculum_nesting] no both-populated units found.')


def _reverse_homework_kind(apps, schema_editor):
    """Reverse: reset kind only, never touch homework/body content."""
    Unit = apps.get_model('content', 'Unit')
    Unit.objects.filter(kind='homework').update(kind='lesson')


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0063_course_access_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='module',
            name='parent',
            field=models.ForeignKey(blank=True, help_text='Parent module when this row is a submodule. Null for a top-level module. Maximum two levels.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='content.module'),
        ),
        migrations.AddField(
            model_name='module',
            name='is_bonus',
            field=models.BooleanField(db_default=False, default=False, help_text='Optional enrichment module (issue #1674). Excluded from the progress denominator; completion is still tracked/shown.'),
        ),
        migrations.AddField(
            model_name='module',
            name='available_after_days',
            field=models.IntegerField(blank=True, help_text="Cohort drip offset for this module, same semantics as Unit.available_after_days (this many days after cohort start_date). Meaningful on any module; the real use is top-level ('week') modules, which also derive the cohort week date range shown to learners from this value. Issue #1674.", null=True),
        ),
        migrations.AddField(
            model_name='unit',
            name='kind',
            field=models.CharField(choices=[('lesson', 'Lesson'), ('homework', 'Homework'), ('event', 'Event')], db_default='lesson', default='lesson', help_text="Element type within the module (issue #1674). 'lesson' is the default so every existing row is correct with no data migration.", max_length=20),
        ),
        migrations.AddField(
            model_name='unit',
            name='session_position',
            field=models.PositiveIntegerField(blank=True, help_text="1-indexed position within the course's live-session series (matches events.Event.series_position). Meaningful only when kind='event'. NOT a FK — the actual Event is resolved per viewer/cohort at render time (issue #1674) since a stored FK would embed one cohort's event into curriculum every cohort shares.", null=True),
        ),
        migrations.AddField(
            model_name='unit',
            name='is_bonus',
            field=models.BooleanField(db_default=False, default=False, help_text='Optional element, even inside a required module (issue #1674). Excluded from the progress denominator; completion is still tracked/shown.'),
        ),
        migrations.RunPython(_backfill_homework_kind, _reverse_homework_kind),
    ]
