from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0093_project_evaluation_criteria'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseextension',
            name='project_passing_score',
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
    ]
