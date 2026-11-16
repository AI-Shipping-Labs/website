from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0094_courseextension_project_passing_score'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseextension',
            name='project_criteria_intro',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='courseextension',
            name='project_criteria_intro_html',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='courseextension',
            name='project_criteria_outro',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='courseextension',
            name='project_criteria_outro_html',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
    ]
