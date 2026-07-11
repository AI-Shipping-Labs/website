from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0021_alter_emailalias_source_emailchangerequest"),
    ]

    operations = [
        migrations.CreateModel(
            name="PrivacyRequestLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "request_type",
                    models.CharField(
                        choices=[("export", "Export"), ("delete", "Delete")],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("completed", "Completed"),
                            ("blocked", "Blocked"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "old_user_id",
                    models.PositiveIntegerField(
                        blank=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                (
                    "normalized_email_hash",
                    models.CharField(db_index=True, max_length=64),
                ),
                (
                    "email_domain",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("row_count_summary", models.JSONField(blank=True, default=dict)),
                (
                    "blocker_reason",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "request_ip_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "user_agent_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
            ],
            options={
                "ordering": ["-requested_at"],
                "indexes": [
                    models.Index(
                        fields=["request_type", "status", "-requested_at"],
                        name="accounts_pr_request_cf965b_idx",
                    ),
                    models.Index(
                        fields=["blocker_reason"],
                        name="accounts_pr_blocker_cc2d98_idx",
                    ),
                ],
            },
        ),
    ]
