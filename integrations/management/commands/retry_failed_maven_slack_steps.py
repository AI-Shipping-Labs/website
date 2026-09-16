"""Force-retry the Maven ``slack`` step for every currently-failed occurrence.

Issue #1665: the ``slack`` step stopped calling the Slack API. It now mirrors
``welcome_status`` instead, because the workspace join link is delivered by
the ``maven_welcome`` email rather than a direct invite. Occurrences whose
``slack_status`` is stuck ``failed`` under the retired invite mechanism need
no raw data migration — they self-heal deterministically once the step is
re-evaluated under the new logic. This command force-retries each one
through the same durable path Studio's "Retry safely" button uses (bypassing
the 3-attempt ceiling) and prints a per-occurrence summary, which is also how
an operator would spot the rare exception where ``welcome_status`` is not
``succeeded`` for a given row.

Usage::

    uv run python manage.py retry_failed_maven_slack_steps
"""

from django.core.management.base import BaseCommand

from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import retry_occurrence_step


class Command(BaseCommand):
    help = "Force-retry the Maven slack step for every occurrence with slack_status=failed."

    def handle(self, *args, **options):
        occurrences = list(
            MavenEnrollmentEvent.objects.filter(
                slack_status=MavenEnrollmentEvent.STEP_FAILED,
            )
            .select_related("user")
            .order_by("pk")
        )

        if not occurrences:
            self.stdout.write("No Maven occurrences with slack_status=failed.")
            return

        for occurrence in occurrences:
            prior_status = occurrence.slack_status
            identity = (
                occurrence.user.email
                if occurrence.user_id
                else (occurrence.email or "(unknown)")
            )
            retry_occurrence_step(occurrence, "slack")
            occurrence.refresh_from_db(fields=["slack_status", "slack_error"])
            self.stdout.write(
                f"occurrence={occurrence.pk} user={identity} "
                f"prior_status={prior_status} resulting_status={occurrence.slack_status} "
                f"note={occurrence.slack_error!r}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"Processed {len(occurrences)} Maven occurrence(s).")
        )
