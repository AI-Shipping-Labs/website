"""Backfill: copy tier/Stripe fields from every User row into Membership (#1579).

Phase 1 (expand) of moving the five membership/billing fields off the user
model. After the ``0017_membership`` table exists, this migration copies
``tier_id``, ``pending_tier_id``, ``billing_period_end``,
``stripe_customer_id`` and ``subscription_id`` from every ``accounts.User``
row into one ``payments.Membership`` row per user, so the "every User row
has exactly one Membership row" invariant holds for pre-existing users too.

Batched in chunks of 1,000 rows (P6 convention) with an upsert so rerunning
the copy is idempotent. Existing rows are refreshed from the compatibility
columns, which also handles a Membership created before a partial run resumes.

Reverse: deletes the Membership rows in batches. Like every data-migration
reverse in this repository, it exists for local/dev reversibility only
(``migrate payments 0017``); a production rollback is image-only and never
reverses migrations (see ``_docs/expand-contract-releases.md``).
"""

from django.db import migrations

BATCH_SIZE = 1000

USER_MEMBERSHIP_FIELDS = (
    "tier_id",
    "pending_tier_id",
    "billing_period_end",
    "stripe_customer_id",
    "subscription_id",
)


def copy_user_fields_to_membership(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Membership = apps.get_model("payments", "Membership")

    last_pk = 0
    while True:
        rows = list(
            User.objects.filter(pk__gt=last_pk)
            .order_by("pk")
            .values("pk", *USER_MEMBERSHIP_FIELDS)[:BATCH_SIZE]
        )
        if not rows:
            break
        last_pk = rows[-1]["pk"]
        Membership.objects.bulk_create(
            (
                Membership(
                    user_id=row["pk"],
                    tier_id=row["tier_id"],
                    pending_tier_id=row["pending_tier_id"],
                    billing_period_end=row["billing_period_end"],
                    stripe_customer_id=row["stripe_customer_id"],
                    subscription_id=row["subscription_id"],
                )
                for row in rows
            ),
            batch_size=BATCH_SIZE,
            update_conflicts=True,
            update_fields=USER_MEMBERSHIP_FIELDS,
            unique_fields=("user",),
        )


def remove_copied_memberships(apps, schema_editor):
    Membership = apps.get_model("payments", "Membership")

    while True:
        pks = list(
            Membership.objects.order_by("pk").values_list("pk", flat=True)[
                :BATCH_SIZE
            ]
        )
        if not pks:
            break
        Membership.objects.filter(pk__in=pks).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0017_membership'),
    ]

    operations = [
        migrations.RunPython(
            copy_user_fields_to_membership,
            remove_copied_memberships,
        ),
    ]
