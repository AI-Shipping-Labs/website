"""Create the ``django_q_cache`` database cache table.

The ``/studio/worker/`` dashboard reads cluster heartbeats from
``CACHES['django_q']`` (a ``DatabaseCache`` backend), which requires a
table created via ``manage.py createcachetable``. Previously this was
run on every container start via ``scripts/entrypoint_init.py``; that
work is more naturally expressed as a Django migration so it runs once
at migrate time and is tracked alongside schema changes.

Lives in ``email_app`` because django-q is the in-process worker behind
SES sends and email_app already owns migrations in the project. The
``django_q`` app is third-party so we don't add migrations there.
"""

from django.core.management import call_command
from django.db import migrations


def create_django_q_cache_table(apps, schema_editor):
    # ``createcachetable`` is idempotent: it checks ``information_schema``
    # (or the SQLite equivalent) for the table and is a no-op if it
    # already exists. Safe to apply on environments where the cold-start
    # entrypoint had already created it.
    call_command("createcachetable", "django_q_cache", verbosity=0)


class Migration(migrations.Migration):

    dependencies = [
        ('email_app', '0012_email_log_ses_event_correlation'),
    ]

    operations = [
        migrations.RunPython(
            create_django_q_cache_table,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
