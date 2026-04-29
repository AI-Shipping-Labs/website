from django.core.management.base import BaseCommand, CommandError

from accounts.services.import_users import get_import_adapter, run_import_batch


class Command(BaseCommand):
    help = "Run a registered external user import adapter"

    def add_arguments(self, parser):
        parser.add_argument("source")
        parser.add_argument("--dry-run", action="store_true", dest="dry_run")
        parser.add_argument("--tags", default="")
        welcome = parser.add_mutually_exclusive_group()
        welcome.add_argument(
            "--send-welcome",
            action="store_true",
            dest="send_welcome",
            default=True,
        )
        welcome.add_argument(
            "--no-send-welcome",
            action="store_false",
            dest="send_welcome",
        )

    def handle(self, *args, **options):
        source = options["source"]
        try:
            adapter = get_import_adapter(source)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if adapter is None:
            raise CommandError(f"No import adapter registered for source: {source}")

        default_tags = [
            tag.strip()
            for tag in (options.get("tags") or "").split(",")
            if tag.strip()
        ]
        batch = run_import_batch(
            source,
            adapter,
            dry_run=options["dry_run"],
            default_tags=default_tags,
            send_welcome=options["send_welcome"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import batch {batch.pk} {batch.status}: "
                f"{batch.users_created} created, {batch.users_updated} updated, "
                f"{batch.users_skipped} skipped, {batch.emails_queued} emails queued"
            )
        )
        if batch.errors:
            self.stdout.write(f"Errors: {len(batch.errors)}")
        if batch.status == batch.STATUS_FAILED:
            raise CommandError(f"Import batch {batch.pk} failed")
