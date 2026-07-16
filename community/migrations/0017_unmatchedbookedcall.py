import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('community', '0016_bookedcall_host_nullable_last_event_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='UnmatchedBookedCall',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_booked_call_id', models.BigIntegerField(blank=True, null=True, unique=True)),
                ('source_created_at', models.DateTimeField(blank=True, null=True)),
                ('source_updated_at', models.DateTimeField(blank=True, null=True)),
                ('invitee_email', models.EmailField(blank=True, default='', max_length=254)),
                ('invitee_name', models.CharField(blank=True, default='', max_length=200)),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('booked', 'Booked'), ('canceled', 'Canceled')], default='booked', max_length=20)),
                ('calendly_event_uri', models.CharField(max_length=500, unique=True)),
                ('calendly_invitee_uri', models.CharField(blank=True, db_index=True, default='', max_length=500)),
                ('scheduling_url', models.URLField(blank=True, default='', max_length=500)),
                ('reschedule_url', models.URLField(blank=True, default='', max_length=500)),
                ('cancel_url', models.URLField(blank=True, default='', max_length=500)),
                ('canceled_at', models.DateTimeField(blank=True, null=True)),
                ('last_event_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('member', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='unmatched_booked_calls', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-scheduled_at', '-created_at']},
        ),
    ]
