from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0024_merge_tieroverride_source_verification_resend_claim')]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='signup_source',
            field=models.CharField(
                choices=[
                    ('unknown', 'Unknown (pre-existing row)'),
                    ('newsletter', 'Newsletter subscribe'),
                    ('download', 'Download request'),
                    ('signup', 'Email + password signup'),
                    ('oauth', 'OAuth signup'),
                    ('imported', 'Bulk import (Stripe / CSV / course DB)'),
                    ('staff_create', 'Staff-created (Studio)'),
                ],
                db_index=True,
                default='unknown',
                help_text='How the user row was created (issue #768).',
                max_length=32,
            ),
        ),
    ]
