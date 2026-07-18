"""Retry-safe convergence for databases that applied the original indexes."""

import importlib

from django.db import migrations


def ensure_observability_indexes(apps, schema_editor):
    migration = importlib.import_module(
        "integrations.migrations.0026_synclog_observability_indexes",
    )
    migration.create_observability_indexes(apps, schema_editor)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("integrations", "0026_synclog_observability_indexes")]

    operations = [
        migrations.RunPython(
            ensure_observability_indexes,
            migrations.RunPython.noop,
        ),
    ]
