"""Add ``Chapter.week_number`` so chapters group into weeks on the roadmap.

A "week" is a set of consecutive chapters read together (chapters 0,1,2 -> week
1; 3,4 -> week 2). Grouping on an integer is robust where the free-text
``week_label`` was not. Existing books that already tagged chapters "Week 1",
"Week 2", ... keep their grouping: the data step parses a leading week number
out of ``week_label`` into ``week_number`` (conservative — only a clear
``Week <n>`` pattern), leaving ``week_label`` untouched as the optional theme.
"""

import re

from django.db import migrations, models

_WEEK_LABEL_RE = re.compile(r'^\s*week\s*0*(\d+)\b', re.IGNORECASE)


def backfill_week_number(apps, schema_editor):
    Chapter = apps.get_model('bookclub', 'Chapter')
    for chapter in Chapter.objects.exclude(week_label='').iterator():
        match = _WEEK_LABEL_RE.match(chapter.week_label or '')
        if match:
            chapter.week_number = int(match.group(1))
            chapter.save(update_fields=['week_number'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('bookclub', '0006_note_body_html_fold_diagram'),
    ]

    operations = [
        migrations.AddField(
            model_name='chapter',
            name='week_number',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                help_text='Optional week grouping. Chapters sharing a '
                          'week_number are read together and render under one '
                          '"Week N" heading on the roadmap (e.g. chapters '
                          '0,1,2 -> week 1; 3,4 -> week 2).',
            ),
        ),
        migrations.RunPython(backfill_week_number, noop_reverse),
        migrations.AlterField(
            model_name='chapter',
            name='week_label',
            field=models.CharField(
                blank=True,
                default='',
                max_length=40,
                help_text='Optional theme/label for the week or chapter, e.g. '
                          '"Batching". When a week_number is set, this '
                          'overrides the default "Week N" heading.',
            ),
        ),
    ]
