import ast
from pathlib import Path

from django.test import SimpleTestCase


class RelayClientContractTest(SimpleTestCase):
    def test_no_new_campaign_loop_calling_transactional_send(self):
        root = Path(__file__).resolve().parents[2]
        # Note: email_app/tasks/send_campaign.py and send_campaign_delivery_to_relay
        # are legacy worker paths owned by intake 0d6cdd71; guard all other
        # services and studio views against looping over transactional sends.
        for directory in (
            root / "studio",
            root / "email_app" / "services",
            root / "events" / "services" / "workshop_ready_notification.py",
        ):
            paths = [directory] if directory.is_file() else sorted(directory.rglob("*.py"))
            for path in paths:
                if path.name == "relay_campaigns.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.For, ast.AsyncFor)):
                        body_src = ast.unparse(node)
                        self.assertNotIn(
                            "/api/transactional/send",
                            body_src,
                            msg=str(path.relative_to(root)),
                        )
                        self.assertNotIn(
                            "send_relay_transactional",
                            body_src,
                            msg=str(path.relative_to(root)),
                        )
