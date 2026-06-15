from django.db import migrations


def backfill_description_html(apps, schema_editor):
    """Re-render description_html for existing Event and EventSeries rows
    through the canonical linkify_urls(render_markdown(...)) pipeline.

    Issue #988: events/series previously stored description_html via bare
    render_markdown (no linkify, no sanitize), and some rows (e.g. event 31)
    had stale or empty description_html that fell through to the raw-text
    template branch.
    """
    from content.utils.markdown import render_description_html

    Event = apps.get_model('events', 'Event')
    EventSeries = apps.get_model('events', 'EventSeries')

    for model in (Event, EventSeries):
        for row in model.objects.all().iterator():
            new_html = render_description_html(row.description or '')
            if row.description_html != new_html:
                row.description_html = new_html
                row.save(update_fields=['description_html'])


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0033_eventseries_required_level'),
    ]

    operations = [
        migrations.RunPython(
            backfill_description_html,
            migrations.RunPython.noop,
        ),
    ]
