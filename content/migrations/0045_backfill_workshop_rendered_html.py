"""Backfill ``Workshop.description_html`` and ``WorkshopPage.body_html`` (issue #791).

The renderer fix from commit ``faf784da`` translates ``<br/>`` inside mermaid
node labels to ``\\n`` so Mermaid 10 (``securityLevel: 'strict'``) renders
the label as a real line break. The fix only runs at ``Workshop.save()`` /
``WorkshopPage.save()`` time, however, and the sync pipeline's
``apply_defaults_if_changed`` short-circuits ``save()`` when the source
markdown is unchanged. Four prod workshops whose READMEs have not changed
since the renderer fix shipped therefore keep their pre-fix ``description_html``
forever and continue to show raw ``flowchart LR`` text instead of the SVG.

This one-shot data migration walks every ``Workshop`` and ``WorkshopPage``
row through the LIVE model's ``save()`` so each row's ``_html`` field is
recomputed by the current ``render_markdown`` pipeline. The forward run is
idempotent — re-rendering already-fresh rows just rewrites the same bytes.
The reverse is a no-op because restoring stale HTML has no operational value.

Live-model rationale: ``apps.get_model('content', 'Workshop')`` returns the
historical model which has NO custom ``save()`` (and therefore would not
call ``render_markdown`` at all). We import the live ``Workshop`` /
``WorkshopPage`` classes from ``content.models`` so the real ``save()``
path runs. This is the same trade-off documented in
``content/migrations/0032_backfill_instructors.py`` (which imports
``render_markdown`` from the live model module for the same reason).

The migration runs automatically on the next ECS deploy via
``scripts/entrypoint_init.py:99`` (``call_command("migrate", ...)``), so the
four broken workshop landings self-heal with no operator step.
"""

from django.db import migrations


def backfill_workshop_rendered_html(apps, schema_editor):
    """Re-render ``description_html`` / ``body_html`` for every existing row.

    Uses ``save(update_fields=[...])`` so the backfill does not touch
    unrelated columns (``source_commit``, ``updated_at``, tags, etc.).
    """
    # Live models — see module docstring for the rationale.
    from content.models.workshop import Workshop, WorkshopPage

    workshop_total = Workshop.objects.count()
    workshop_done = 0
    for workshop in Workshop.objects.all().iterator():
        workshop.save(update_fields=['description_html'])
        workshop_done += 1
        if workshop_done % 100 == 0:
            print(
                f"  [backfill_workshop_rendered_html] "
                f"Backfilled {workshop_done}/{workshop_total} workshops",
                flush=True,
            )

    page_total = WorkshopPage.objects.count()
    page_done = 0
    for page in WorkshopPage.objects.all().iterator():
        page.save(update_fields=['body_html'])
        page_done += 1
        if page_done % 100 == 0:
            print(
                f"  [backfill_workshop_rendered_html] "
                f"Backfilled {page_done}/{page_total} workshop pages",
                flush=True,
            )

    print(
        f"  [backfill_workshop_rendered_html] "
        f"Backfilled {workshop_done} workshops, {page_done} workshop pages",
        flush=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0044_add_auto_banner_fields'),
    ]

    operations = [
        migrations.RunPython(
            backfill_workshop_rendered_html,
            migrations.RunPython.noop,
        ),
    ]
