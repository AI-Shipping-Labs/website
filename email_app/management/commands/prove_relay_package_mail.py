"""Send a fixed-recipient Relay package mail proof without production fixtures."""

import re
from datetime import datetime, timezone
from uuid import uuid4

from community_base.mail.jobs import deliver as deliver_job
from django.core.management.base import BaseCommand, CommandError

from email_app.package_mail import send_package_mail

RECIPIENT_EMAIL = "alexey.s.grigoriev@gmail.com"
DEFAULT_TEMPLATE = "onboarding_reminder"
SAFE_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,199}")


def _validate_key(value: str) -> str:
    if SAFE_KEY_PATTERN.fullmatch(value):
        return value
    raise CommandError("--key must contain only letters, digits, colons, underscores, or hyphens")


class Command(BaseCommand):
    help = "Build or send the fixed-recipient Relay package mail proof (alexey.s.grigoriev@gmail.com only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send",
            action="store_true",
            help="submit the proof to Relay; the default is a dry run",
        )
        parser.add_argument(
            "--template",
            default=DEFAULT_TEMPLATE,
            help=f"Relay template key to test (default: {DEFAULT_TEMPLATE})",
        )
        parser.add_argument(
            "--key",
            default="",
            help="stable idempotency key for this proof run",
        )

    def handle(self, *args, **options):
        template_name = options["template"]
        idempotency_key = (
            _validate_key(options["key"])
            if options["key"]
            else f"proof:package-mail:{template_name}:{int(datetime.now(timezone.utc).timestamp())}:{uuid4().hex[:8]}"
        )

        context = {
            "user_name": "Alexey",
        }

        if not options["send"]:
            self.stdout.write(
                f"dry_run recipient={RECIPIENT_EMAIL} template={template_name} idempotency_key={idempotency_key}"
            )
            return

        try:
            delivery = send_package_mail(
                None,
                template_name,
                context,
                recipient_email=RECIPIENT_EMAIL,
                idempotency_key=idempotency_key,
            )
            # Synchronously deliver through configured backend
            deliver_job(None, {"delivery_id": str(delivery.id)})
            delivery.refresh_from_db()
        except Exception as exc:
            raise CommandError(f"Relay package proof failed: {exc}") from exc

        self.stdout.write(
            f"status={delivery.state} "
            f"template={template_name} "
            f"message_id={delivery.external_message_id} "
            f"delivery_id={delivery.id} "
            f"idempotency_key={delivery.idempotency_key}"
        )
