import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('content', '0053_merge_0052_marketingpage_0052_workshop_core_tools')]

    operations = [
        migrations.AddField(
            model_name='download',
            name='asset_mime_type',
            field=models.CharField(
                blank=True,
                db_default='',
                default='',
                help_text='Validated MIME type for the private asset. Legacy `other` rows remain stored but are not deliverable unless a future policy adds an approved extension/MIME pair.',
                max_length=150,
            ),
        ),
        migrations.AddField(
            model_name='download',
            name='storage_key',
            field=models.CharField(
                blank=True,
                db_default='',
                default='',
                help_text='Private S3 object key. Required for secure delivery; never rendered on public surfaces.',
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name='download',
            name='delivery_blocked_reason',
            field=models.CharField(
                blank=True,
                db_default='',
                default='',
                help_text='Operator-facing readiness marker set when source validation fails. Never rendered on public surfaces.',
                max_length=200,
            ),
        ),
        migrations.CreateModel(
            name='DownloadDeliveryGrant',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('token_hash', models.CharField(editable=False, max_length=64, unique=True)),
                ('newsletter_opt_in', models.BooleanField(default=False)),
                ('surface', models.CharField(default='detail', max_length=20)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('redeemed_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('download', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delivery_grants', to='content.download')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='download_delivery_grants', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='downloaddeliverygrant',
            index=models.Index(fields=['download', 'expires_at'], name='content_dlgrant_dl_exp_idx'),
        ),
    ]
