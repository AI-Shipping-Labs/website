from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0089_alter_enrollment_source'),
    ]

    operations = [
        migrations.AlterField(
            model_name='courseproject',
            name='submission_due_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='courseproject',
            name='review_due_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
