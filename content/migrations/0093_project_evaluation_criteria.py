from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0092_courseannouncement_cohort'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseproject',
            name='evaluation_criteria',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='peerreview',
            name='criteria_scores',
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
    ]
