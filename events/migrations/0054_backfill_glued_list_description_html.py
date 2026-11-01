from django.db import migrations


def backfill_description_html(apps, schema_editor):
    """Re-render description_html for existing Event and EventSeries rows.

    Lists written directly under a paragraph line (``What we will cover:``
    followed immediately by ``-`` / ``1.`` lines, with no blank line between)
    previously stored leaked HTML with literal ``-`` characters, because
    description_html is precomputed on save and Python-Markdown only forms a
    list after a blank line. The render pipeline now inserts that blank line
    (``_ensure_blank_line_before_lists``); re-rendering every row fixes the
    MCP server workshop event and all legacy rows on deploy. Deterministic
    and idempotent. ``recap_html`` is sync-owned with include expansion and
    is deliberately untouched; ``recap_notes_html`` goes through the same
    pipeline as descriptions, so it is covered here.
    """
    from content.utils.markdown import render_description_html

    Event = apps.get_model('events', 'Event')
    EventSeries = apps.get_model('events', 'EventSeries')

    for row in Event.objects.all().iterator():
        new_description_html = render_description_html(row.description or '')
        new_recap_notes_html = render_description_html(row.recap_notes or '')
        if (row.description_html != new_description_html
                or row.recap_notes_html != new_recap_notes_html):
            row.description_html = new_description_html
            row.recap_notes_html = new_recap_notes_html
            row.save(update_fields=['description_html', 'recap_notes_html'])

    for row in EventSeries.objects.all().iterator():
        new_html = render_description_html(row.description or '')
        if row.description_html != new_html:
            row.description_html = new_html
            row.save(update_fields=['description_html'])


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0053_backfill_heading_ids'),
    ]

    operations = [
        migrations.RunPython(
            backfill_description_html,
            migrations.RunPython.noop,
        ),
    ]
