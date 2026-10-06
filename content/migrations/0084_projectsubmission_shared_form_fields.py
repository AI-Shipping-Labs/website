from django.db import migrations, models


class Migration(migrations.Migration):
    """Shared project submission form fields (issue #1777).

    Plain nullable/defaulted additions: existing rows keep their ids, URLs,
    descriptions, statuses, reviews and cohort links; the new columns read
    back empty/blank for historical submissions.
    """

    dependencies = [
        ('content', '0083_homeworkstepthread'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectsubmission',
            name='commit_id',
            field=models.CharField(
                blank=True,
                db_default='',
                default='',
                help_text=(
                    'Short commit hash (7-40 hex characters) of the reviewed version.'
                ),
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='learning_in_public_links',
            field=models.JSONField(
                blank=True,
                db_default=[],
                default=list,
                help_text='Public progress post links, capped by the shared form contract.',
            ),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='time_spent',
            field=models.FloatField(
                blank=True,
                null=True,
                help_text='Hours the learner spent on the project.',
            ),
        ),
    ]
