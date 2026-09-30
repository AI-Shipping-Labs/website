"""Rename two AISL reverse accessors on User (#1696 F2).

``community_base.coursework`` declares ``homework_submissions`` and
``project_submissions`` on User, so installing it clashes (E304) with
``content.Submission.student`` and ``content.ProjectSubmission.user``.
``related_name`` is Python-only, so this is state-only: no SQL runs.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0079_backfill_heading_ids'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='projectsubmission',
                    name='user',
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='aisl_project_submissions',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AlterField(
                    model_name='submission',
                    name='student',
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='aisl_homework_submissions',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
