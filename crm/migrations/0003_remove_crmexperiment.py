"""Drop the ``CRMExperiment`` model and its table (issue #590).

The Experiments feature was removed entirely — UI, views, URLs, model,
admin, and tests. This migration drops the ``crm_crmexperiment`` table
in one step. No backwards-compat shim, no preserved data, no soft
delete; full removal per the user's explicit instruction.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0002_backfill_records_from_notes'),
    ]

    operations = [
        migrations.DeleteModel(
            name='CRMExperiment',
        ),
    ]
