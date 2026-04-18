"""Backfill Module.overview from existing README-as-Unit rows.

Issue #222: prior to this change, module READMEs were synced as a Unit row
with ``slug='readme'`` and ``sort_order=-1``. We now store the README body
on ``Module.overview`` instead. This migration copies the body from each
such Unit onto its parent module, then deletes the Unit so the README no
longer shows up in the lesson list / lesson counts.

Also migrates any ``UserCourseProgress`` rows that pointed at the README
unit so the user's lifetime "completed lessons" history isn't lost — they
are deleted along with the unit (the cascade handles that), but we don't
try to preserve them on the module since "completed the overview" isn't a
concept the new layout supports.
"""
from django.db import migrations


def backfill_overview_from_readme_units(apps, schema_editor):
    Module = apps.get_model('content', 'Module')
    Unit = apps.get_model('content', 'Unit')

    # README units are identified by slug='readme' AND sort_order=-1 — the
    # exact pair that the sync used to create. Filtering on both avoids
    # touching real units that an author might have called "readme".
    readme_units = Unit.objects.filter(slug='readme', sort_order=-1)

    # Group by module to handle the "multiple readme units per module" edge
    # case (shouldn't happen, but be defensive).
    seen_module_ids = set()
    units_to_delete = []
    for unit in readme_units.select_related('module').order_by('module_id', 'pk'):
        module_id = unit.module_id
        if module_id in seen_module_ids:
            # Module already got its overview from an earlier unit — just
            # mark this duplicate for deletion.
            units_to_delete.append(unit.pk)
            continue
        seen_module_ids.add(module_id)

        try:
            module = Module.objects.get(pk=module_id)
        except Module.DoesNotExist:
            units_to_delete.append(unit.pk)
            continue

        module.overview = unit.body or ''
        # The Module model's save() method renders overview_html, but data
        # migrations use the historical model which has no custom save().
        # Render here using the same pipeline so the HTML is correct.
        module.overview_html = _render_overview_html(
            module.overview, module.title,
        )
        module.overview_source_path = unit.source_path or ''
        module.save(update_fields=[
            'overview', 'overview_html', 'overview_source_path',
        ])
        units_to_delete.append(unit.pk)

    if units_to_delete:
        Unit.objects.filter(pk__in=units_to_delete).delete()


def _render_overview_html(overview_md, module_title):
    """Render module overview markdown to HTML in a data-migration-safe way.

    Re-implements the pipeline from ``content.models.course.Module.save``:
    strip a leading H1 that duplicates the module title, then render with
    the project's markdown extensions, then linkify bare URLs.
    """
    if not overview_md:
        return ''
    # Imports here are safe — they only depend on app code, not on the
    # historical ORM models.
    from content.models.course import render_markdown
    from content.utils.h1 import strip_leading_title_h1
    from content.utils.linkify import linkify_urls
    md = strip_leading_title_h1(overview_md, module_title or '')
    return linkify_urls(render_markdown(md))


def reverse_backfill(apps, schema_editor):
    """Recreate README Unit rows from Module.overview, then clear overview.

    Best-effort reverse — used if the migration is rolled back. We can't
    fully restore the original Unit (lost: content_id, content_hash,
    source_repo, source_commit, completion records) but we restore enough
    that the old code path renders something.
    """
    Module = apps.get_model('content', 'Module')
    Unit = apps.get_model('content', 'Unit')

    for module in Module.objects.exclude(overview=''):
        # Skip if a readme unit already exists (defensive).
        if Unit.objects.filter(
            module=module, slug='readme', sort_order=-1,
        ).exists():
            continue
        Unit.objects.create(
            module=module,
            title=module.title,
            slug='readme',
            sort_order=-1,
            body=module.overview,
            body_html=module.overview_html,
            source_path=module.overview_source_path or '',
            source_repo=module.source_repo,
            source_commit=module.source_commit,
        )
        module.overview = ''
        module.overview_html = ''
        module.overview_source_path = None
        module.save(update_fields=[
            'overview', 'overview_html', 'overview_source_path',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0026_add_module_overview'),
    ]

    operations = [
        migrations.RunPython(
            backfill_overview_from_readme_units,
            reverse_backfill,
        ),
    ]
