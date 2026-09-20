"""One-off push of AISL contact state into Relay (plan issue A6.2).

Batched and resumable: iterate users ordered by pk in chunks, and on a
Relay failure stop with the resume point so the operator can continue
with ``--after-id``. Every upsert is idempotent by email, so a re-run
creates no new contacts. Output is redacted per architecture rule 8:
counts only, never email addresses.
"""

from community_base.mail.relay_contacts import RelayContactsError
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from email_app import relay_sync

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Push every user's contact state (subscription, verification, tags and"
        " tier tag) to the AISL Relay audience."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="print the planned count only"
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="users per iteration chunk (default 200)",
        )
        parser.add_argument(
            "--after-id",
            type=int,
            default=0,
            help="resume: process only users with pk greater than this",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        after_id = options["after_id"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1")

        total = User.objects.filter(pk__gt=after_id).count()
        if dry_run:
            self.stdout.write(f"dry run: would sync {total} contacts")
            return

        client = relay_sync.contacts_client()
        synced = 0
        last_id = after_id
        try:
            users = (
                User.objects.filter(pk__gt=after_id)
                .order_by("pk")
                .select_related("tier")
                .prefetch_related("member_extra__contact_tags")
                .iterator(chunk_size=batch_size)
            )
            for user in users:
                relay_sync.sync_user_to_relay(user, client)
                synced += 1
                last_id = user.pk
        except RelayContactsError as error:
            raise CommandError(
                f"relay sync failed ({error.code}) after {synced} contacts;"
                f" resume with --after-id {last_id}"
            ) from error
        self.stdout.write(
            f"synced {synced} contacts; resume point --after-id {last_id}"
        )
