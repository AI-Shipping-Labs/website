from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from email_app.management.commands.prove_relay_package_mail import (
    DEFAULT_TEMPLATE,
    RECIPIENT_EMAIL,
)


class RelayPackageProofCommandTest(SimpleTestCase):
    def test_default_is_network_free(self):
        output = StringIO()

        with patch(
            "email_app.management.commands.prove_relay_package_mail.send_package_mail",
        ) as send_package_mail, patch(
            "email_app.management.commands.prove_relay_package_mail.deliver_job",
        ) as deliver_job:
            call_command(
                "prove_relay_package_mail",
                "--key=proof:package-mail:test",
                stdout=output,
            )

        send_package_mail.assert_not_called()
        deliver_job.assert_not_called()
        self.assertIn("dry_run", output.getvalue())
        self.assertIn(f"template={DEFAULT_TEMPLATE}", output.getvalue())

    def test_send_uses_site_boundary_with_fixed_recipient_and_key(self):
        key = "proof:package-mail:test"
        delivery = SimpleNamespace(
            id="delivery-test",
            state="sent",
            external_message_id="relay-test-message",
            idempotency_key=key,
            refresh_from_db=lambda: None,
        )
        output = StringIO()

        with patch(
            "email_app.management.commands.prove_relay_package_mail.send_package_mail",
            return_value=delivery,
        ) as send_package_mail, patch(
            "email_app.management.commands.prove_relay_package_mail.deliver_job",
        ) as deliver_job:
            call_command(
                "prove_relay_package_mail",
                "--send",
                f"--key={key}",
                stdout=output,
            )

        send_package_mail.assert_called_once_with(
            None,
            DEFAULT_TEMPLATE,
            {"user_name": "Alexey"},
            recipient_email=RECIPIENT_EMAIL,
            idempotency_key=key,
        )
        deliver_job.assert_called_once_with(
            None,
            {"delivery_id": "delivery-test"},
        )
        self.assertIn("status=sent", output.getvalue())
        self.assertIn("message_id=relay-test-message", output.getvalue())
