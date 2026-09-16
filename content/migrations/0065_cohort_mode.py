"""Cohort.mode: cohort/self_paced delivery mode (issue #1674).

Own migration — different model (``content/models/cohort.py``) than the
0064 Module/Unit migration, no ordering dependency between the two.

``mode`` ships with ``db_default='cohort'`` so every existing row keeps
its current meaning with no data migration and old-container inserts
mid-rolling-deploy still satisfy the NOT NULL constraint.

``start_date``/``end_date`` relaxing from required to nullable is the one
deliberate ``AlterField`` in this issue: safe because it only widens what
is accepted (every existing row already has both set, so no existing row
is affected) and needs no ``db_default`` (nullable columns don't need
one).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0064_curriculum_nesting'),
    ]

    operations = [
        migrations.AddField(
            model_name='cohort',
            name='mode',
            field=models.CharField(choices=[('cohort', 'Cohort'), ('self_paced', 'Self-paced')], db_default='cohort', default='cohort', help_text="'cohort' (default): a dated, time-bound cohort — today's behaviour. 'self_paced': no dates, no event series, unlimited capacity. Matches curriculum.Cohort.mode in the future community_base package exactly (issue #1674).", max_length=20),
        ),
        migrations.AlterField(
            model_name='cohort',
            name='start_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='cohort',
            name='end_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='cohort',
            constraint=models.UniqueConstraint(condition=models.Q(('mode', 'self_paced')), fields=('course',), name='cohort_self_paced_unique_per_course'),
        ),
    ]
