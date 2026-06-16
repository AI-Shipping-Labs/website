"""Backfill stored markdown HTML after adding bare-URL linkification.

Issue #1000 adds a ``linkify_urls(render_markdown(...))`` pass to workshop
descriptions, workshop pages, and instructor bios. Existing rows keep their
previous stored HTML until the source markdown changes, so this migration
walks those rows through the live model ``save()`` implementations.

The reverse is a no-op because restoring stale non-linkified HTML has no
operational value.
"""

from django.db import migrations


def backfill_linkified_markdown_html(apps, schema_editor):
    """Re-render stored workshop and instructor HTML fields."""
    from content.models.instructor import Instructor
    from content.models.workshop import Workshop, WorkshopPage

    workshop_total = Workshop.objects.count()
    workshop_done = 0
    for workshop in Workshop.objects.only(
        'id', 'description', 'description_html',
    ).iterator():
        workshop.save(update_fields=['description_html'])
        workshop_done += 1
        if workshop_done % 100 == 0:
            print(
                f"  [backfill_linkified_markdown_html] "
                f"Backfilled {workshop_done}/{workshop_total} workshops",
                flush=True,
            )

    page_total = WorkshopPage.objects.count()
    page_done = 0
    for page in WorkshopPage.objects.only('id', 'body', 'body_html').iterator():
        page.save(update_fields=['body_html'])
        page_done += 1
        if page_done % 100 == 0:
            print(
                f"  [backfill_linkified_markdown_html] "
                f"Backfilled {page_done}/{page_total} workshop pages",
                flush=True,
            )

    instructor_total = Instructor.objects.count()
    instructor_done = 0
    for instructor in Instructor.objects.only('id', 'bio', 'bio_html').iterator():
        instructor.save(update_fields=['bio_html'])
        instructor_done += 1
        if instructor_done % 100 == 0:
            print(
                f"  [backfill_linkified_markdown_html] "
                f"Backfilled {instructor_done}/{instructor_total} instructors",
                flush=True,
            )

    print(
        f"  [backfill_linkified_markdown_html] Backfilled "
        f"{workshop_done} workshops, {page_done} workshop pages, "
        f"{instructor_done} instructors",
        flush=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0047_coursecertificate_revoked_at_and_more'),
    ]

    operations = [
        migrations.RunPython(
            backfill_linkified_markdown_html,
            migrations.RunPython.noop,
        ),
    ]
