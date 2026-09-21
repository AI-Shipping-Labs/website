from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0072_courseproject_module'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseextension',
            name='reader_navigation_scope',
            field=models.CharField(
                choices=[('course', 'Entire course'), ('submodule', 'Current submodule')],
                db_default='course',
                default='course',
                max_length=20,
            ),
        ),
    ]
