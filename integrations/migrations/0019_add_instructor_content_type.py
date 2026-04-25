"""Add 'instructor' to ContentSource.content_type choices (issue #308).

Mirrors the previous AlterField pattern from 0018; only adds a new option to
the choices list.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0018_alter_contentsource_content_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contentsource',
            name='content_type',
            field=models.CharField(
                choices=[
                    ('article', 'Article'),
                    ('course', 'Course'),
                    ('resource', 'Resource'),
                    ('project', 'Project'),
                    ('interview_question', 'Interview Question'),
                    ('event', 'Event'),
                    ('workshop', 'Workshop'),
                    ('instructor', 'Instructor'),
                ],
                help_text='Type of content this repo contains.',
                max_length=30,
            ),
        ),
    ]
