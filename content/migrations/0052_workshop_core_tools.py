from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0051_workshop_skill_level'),
    ]

    operations = [
        migrations.AddField(
            model_name='workshop',
            name='core_tools',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Ordered list of tool/technology display names authored '
                    'in workshop.yaml under `core_tools:`. Used for public '
                    'workshop catalog filtering.'
                ),
            ),
        ),
    ]
