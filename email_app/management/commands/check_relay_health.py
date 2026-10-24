"""Management command to check Relay health and configuration (AC 5)."""

import json

from django.core.management.base import BaseCommand, CommandError

from email_app.health import check_relay_availability, get_relay_health_status


class Command(BaseCommand):
    help = "Verify Relay configuration, readiness, and dry-run guard (mirror CMP 061e8607)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output health status as JSON.",
        )
        parser.add_argument(
            "--check-ready",
            action="store_true",
            help="Also probe Relay /health/ready endpoint.",
        )

    def handle(self, *args, **options):
        status = get_relay_health_status()
        if options["check_ready"] and status["status"] == "ok":
            ready, reason = check_relay_availability()
            if not ready:
                status["status"] = "FAIL"
                status["relay_status"] = "FAIL"
                status["degraded"] = True
                status["error"] = reason

        if options["json"]:
            self.stdout.write(json.dumps(status, indent=2))
        else:
            self.stdout.write(f"Relay Status: {status['status']} (Relay: {status['relay_status']})")
            if status.get("error"):
                self.stderr.write(f"Error: {status['error']}")

        if status["status"] != "ok":
            raise CommandError(f"Relay health check FAIL: {status.get('error', 'unhealthy')}")
