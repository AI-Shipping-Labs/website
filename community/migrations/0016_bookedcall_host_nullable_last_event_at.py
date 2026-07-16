from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('community', '0015_alter_communityauditlog_action')]

    operations = [
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
