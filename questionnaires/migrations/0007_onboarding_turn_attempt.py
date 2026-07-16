import django.db.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('questionnaires', '0006_update_onboarding_questionnaire_copy_1099')]

    operations = [
        migrations.AddField(
            model_name='onboardingconversation',
            name='turn_version',
            field=models.PositiveBigIntegerField(db_default=0, default=0),
        ),
        migrations.CreateModel(
            name='OnboardingTurnAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('request_id', models.UUIDField()),
                ('member_message_hash', models.CharField(max_length=64)),
                ('admitted_version', models.PositiveBigIntegerField()),
                ('transport', models.CharField(choices=[('stream', 'Stream'), ('non_stream', 'Non-stream')], max_length=16)),
                ('status', models.CharField(choices=[('processing', 'Processing'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], default='processing', max_length=16)),
                ('outcome', models.CharField(blank=True, default='', max_length=32)),
                ('error_code', models.CharField(blank=True, default='', max_length=32)),
                ('provider', models.CharField(blank=True, default='', max_length=32)),
                ('model', models.CharField(blank=True, default='', max_length=120)),
                ('provider_call_count', models.PositiveSmallIntegerField(default=0)),
                ('retry_count', models.PositiveSmallIntegerField(default=0)),
                ('fallback_used', models.BooleanField(default=False)),
                ('timed_out', models.BooleanField(default=False)),
                ('disconnected', models.BooleanField(default=False)),
                ('input_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('output_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('cache_read_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('cache_write_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('started_at', models.DateTimeField()),
                ('provider_started_at', models.DateTimeField(blank=True, null=True)),
                ('first_delta_at', models.DateTimeField(blank=True, null=True)),
                ('last_delta_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('lease_expires_at', models.DateTimeField()),
                ('admission_to_provider_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('ttft_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('provider_duration_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('persistence_tail_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('persistence_to_done_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('total_duration_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('notification_status', models.CharField(choices=[('not_needed', 'Not needed'), ('pending', 'Pending'), ('processing', 'Processing'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], default='not_needed', max_length=16)),
                ('notification_attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('notification_lease_expires_at', models.DateTimeField(blank=True, null=True)),
                ('notification_last_error', models.CharField(blank=True, default='', max_length=120)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='turn_attempts', to='questionnaires.onboardingconversation')),
            ],
        ),
        migrations.AddConstraint(
            model_name='onboardingturnattempt',
            constraint=models.UniqueConstraint(fields=('conversation', 'request_id'), name='unique_onboarding_turn_request'),
        ),
        migrations.AddConstraint(
            model_name='onboardingturnattempt',
            constraint=models.UniqueConstraint(condition=django.db.models.Q(('status', 'processing')), fields=('conversation',), name='one_processing_onboarding_turn'),
        ),
        migrations.AddIndex(
            model_name='onboardingturnattempt',
            index=models.Index(fields=['status', 'lease_expires_at'], name='questionnai_status_1aef50_idx'),
        ),
    ]
