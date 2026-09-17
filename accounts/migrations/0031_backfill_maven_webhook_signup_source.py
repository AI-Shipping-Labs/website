"""Correct the ``signup_source`` of accounts the Maven webhook created (#1732).

Before this release the webhook stamped its own accounts ``imported``, so
Studio labelled them ``Bulk import (Stripe / CSV / course DB)``. The
predicate is purely in-database and non-heuristic: a user linked to a
``MavenEnrollmentEvent`` that recorded ``account_created=True``. Only rows
whose current value is exactly ``imported`` are touched, so a member who
later signed up, connected OAuth, or was created by staff keeps their real
origin.
"""

from django.db import migrations


def backfill_maven_webhook_signup_source(apps, schema_editor):
    MavenEnrollmentEvent = apps.get_model("integrations", "MavenEnrollmentEvent")
    User = apps.get_model("accounts", "User")

    user_ids = (
        MavenEnrollmentEvent.objects.filter(
            account_created=True,
            user__isnull=False,
        )
        .values_list("user_id", flat=True)
        .distinct()
    )
    User.objects.filter(
        pk__in=list(user_ids),
        signup_source="imported",
    ).update(signup_source="maven_webhook")


def restore_imported_signup_source(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(signup_source="maven_webhook").update(
        signup_source="imported",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0030_alter_user_signup_source"),
        ("integrations", "0034_mavenenrollmentevent_tagging_attempted_at_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_maven_webhook_signup_source,
            restore_imported_signup_source,
        ),
    ]
