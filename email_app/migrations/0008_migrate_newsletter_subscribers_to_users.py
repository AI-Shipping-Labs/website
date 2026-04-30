"""Reconcile legacy newsletter subscribers into accounts.User.

Production pre-checks before applying this migration:

1. Count legacy rows and mapping shape:
   SELECT
     COUNT(*) AS total,
     COUNT(*) FILTER (WHERE is_active) AS active,
     COUNT(*) FILTER (WHERE NOT is_active) AS inactive
   FROM email_app_newslettersubscriber;

2. Count case-insensitive duplicate legacy emails:
   SELECT LOWER(email) AS email, COUNT(*)
   FROM email_app_newslettersubscriber
   GROUP BY LOWER(email)
   HAVING COUNT(*) > 1;

3. Count rows with and without matching users:
   SELECT
     COUNT(u.id) AS matching_users,
     COUNT(*) FILTER (WHERE u.id IS NULL) AS subscriber_only
   FROM email_app_newslettersubscriber ns
   LEFT JOIN accounts_user u ON LOWER(u.email) = LOWER(ns.email);

4. Inspect conflicting matching-user states before deploy:
   SELECT ns.is_active, u.unsubscribed,
          u.email_preferences->>'newsletter' AS pref_newsletter,
          COUNT(*)
   FROM email_app_newslettersubscriber ns
   JOIN accounts_user u ON LOWER(u.email) = LOWER(ns.email)
   GROUP BY ns.is_active, u.unsubscribed, pref_newsletter
   ORDER BY ns.is_active DESC, u.unsubscribed, pref_newsletter;

Decision: subscriber-only emails are preserved as free, email-verified User
rows with unusable passwords. Legacy ``is_active=True`` maps to
``unsubscribed=False`` and ``email_preferences.newsletter=True``; inactive
rows map to ``unsubscribed=True`` and ``email_preferences.newsletter=False``.
Reverse migration recreates the legacy table from canonical User state.
"""

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import BaseUserManager
from django.db import migrations
from django.utils import timezone


def _newsletter_preferences(existing, is_active):
    preferences = dict(existing or {})
    preferences["newsletter"] = bool(is_active)
    return preferences


def _legacy_rows_by_email(NewsletterSubscriber):
    rows = {}
    for subscriber in NewsletterSubscriber.objects.order_by("pk"):
        email = (subscriber.email or "").strip()
        if not email:
            continue
        key = email.lower()
        if key not in rows:
            rows[key] = {
                "email": BaseUserManager.normalize_email(email.lower()),
                "is_active": bool(subscriber.is_active),
                "subscribed_at": subscriber.subscribed_at,
            }
            continue
        rows[key]["is_active"] = rows[key]["is_active"] or bool(subscriber.is_active)
        if subscriber.subscribed_at and (
            not rows[key]["subscribed_at"]
            or subscriber.subscribed_at < rows[key]["subscribed_at"]
        ):
            rows[key]["subscribed_at"] = subscriber.subscribed_at
    return rows


def migrate_subscribers_to_users(apps, schema_editor):
    NewsletterSubscriber = apps.get_model("email_app", "NewsletterSubscriber")
    User = apps.get_model("accounts", "User")
    Tier = apps.get_model("payments", "Tier")

    legacy_rows = _legacy_rows_by_email(NewsletterSubscriber)
    if not legacy_rows:
        return

    existing_users = {user.email.lower(): user for user in User.objects.all()}
    free_tier = Tier.objects.filter(slug="free").first()
    migrated_at = timezone.now()

    for key, row in legacy_rows.items():
        is_active = row["is_active"]
        user = existing_users.get(key)
        if user is None:
            user = User(
                email=row["email"],
                password=make_password(None),
                email_verified=True,
                unsubscribed=not is_active,
                email_preferences={"newsletter": is_active},
                date_joined=row["subscribed_at"] or migrated_at,
                imported_at=migrated_at,
                import_metadata={
                    "legacy_newsletter_subscriber": {
                        "is_active": is_active,
                        "migrated_at": migrated_at.isoformat(),
                    },
                },
            )
            if free_tier is not None:
                user.tier = free_tier
            user.save()
            continue

        user.unsubscribed = not is_active
        user.email_preferences = _newsletter_preferences(
            user.email_preferences,
            is_active,
        )
        if is_active:
            user.email_verified = True
        metadata = dict(user.import_metadata or {})
        metadata["legacy_newsletter_subscriber"] = {
            "is_active": is_active,
            "migrated_at": migrated_at.isoformat(),
        }
        user.import_metadata = metadata
        user.save(
            update_fields=[
                "unsubscribed",
                "email_preferences",
                "email_verified",
                "import_metadata",
            ],
        )


def recreate_subscribers_from_users(apps, schema_editor):
    NewsletterSubscriber = apps.get_model("email_app", "NewsletterSubscriber")
    User = apps.get_model("accounts", "User")

    subscribers = []
    for user in User.objects.all().order_by("pk"):
        preferences = dict(user.email_preferences or {})
        is_active = bool(preferences.get("newsletter", not user.unsubscribed))
        if user.unsubscribed:
            is_active = False
        subscribers.append(
            NewsletterSubscriber(
                email=user.email,
                is_active=is_active,
            )
        )
    NewsletterSubscriber.objects.bulk_create(subscribers, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_user_preferred_timezone"),
        ("email_app", "0007_emailcampaign_slack_filter"),
    ]

    operations = [
        migrations.RunPython(
            migrate_subscribers_to_users,
            recreate_subscribers_from_users,
        ),
        migrations.DeleteModel(
            name="NewsletterSubscriber",
        ),
    ]
