from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0074_alter_courseextension_reader_navigation_scope'),
    ]

    operations = [
        migrations.AddField(
            model_name='homework',
            name='stepper_enabled',
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
