import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('community', '0015_alter_communityauditlog_action')]

    operations = [
        migrations.AlterField(
            model_name='bookedcall',
            name='host',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='booked_calls',
                to='community.callhost',
            ),
        ),
        migrations.AddField(
            model_name='bookedcall',
            name='last_event_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Calendly delivery time used to reject stale/out-of-order state changes.',
                null=True,
            ),
        ),
    ]
