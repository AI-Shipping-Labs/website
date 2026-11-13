import ast
from pathlib import Path

from django.test import SimpleTestCase


class RelayClientContractTest(SimpleTestCase):
    def test_no_new_campaign_loop_calling_transactional_send(self):
        root = Path(__file__).resolve().parents[2]
        # Note: email_app/tasks/send_campaign.py and send_campaign_delivery_to_relay
        # are legacy worker paths owned by intake 0d6cdd71; guard all other
        # services, event fan-outs, and studio views against looping over
        # per-recipient transactional/package sends.
        targets = (
            root / "studio",
            root / "email_app" / "services",
            root / "events" / "tasks" / "notify_reschedule.py",
            root / "events" / "tasks" / "notify_cancellation.py",
            root / "events" / "tasks" / "send_post_event_followup.py",
            root / "events" / "services" / "series_invite.py",
            root / "events" / "services" / "recording_ready_notification.py",
            root / "events" / "services" / "workshop_ready_notification.py",
        )
        forbidden = (
            "/api/transactional/send",
            "send_relay_transactional",
            "_send_calendar_email",
            "send_package_mail",
        )
        for target in targets:
            paths = [target] if target.is_file() else sorted(target.rglob("*.py"))
            for path in paths:
                if path.name == "relay_campaigns.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.For, ast.AsyncFor)):
                        body_src = ast.unparse(node)
                        for symbol in forbidden:
                            if path.name == "series_invite.py" and symbol == "_send_calendar_email":
                                continue
                            self.assertNotIn(
                                symbol,
                                body_src,
                                msg=str(path.relative_to(root)),
                            )
