from django.db import migrations


def backfill_description_html(apps, schema_editor):
    """Re-render description_html for existing Event and EventSeries rows.

    Issue #1126: descriptions authored with inline dash-runs
    (``We will focus on: - a - b - c``) previously stored leaked HTML with
    literal ``-`` characters, because description_html is precomputed on save.
    The render pipeline now normalizes inline dash-runs into real ``<ul>`` list
    items (``normalize_inline_bullets``); re-rendering every row fixes event 37
    and all legacy rows on deploy. Deterministic and idempotent.
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
        ('events', '0038_host_title'),
    ]

    operations = [
        migrations.RunPython(
            backfill_description_html,
            migrations.RunPython.noop,
        ),
    ]
