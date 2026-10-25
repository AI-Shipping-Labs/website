from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from email_app.services.relay_templates import RelayTemplateError


def relay_template(key="welcome", **changes):
    template = {
        "key": key,
        "name": "Welcome",
        "description": "Sent to new members.",
        "subject": "Welcome, {{ user_name }}",
        "html_body": "",
        "text_body": "",
        "markdown_body": "Hello {{ user_name }}",
        "category": "transactional",
        "required_context": ["user_name"],
        "example_context": {"user_name": "Ada"},
        "default_sender_id": "transactional",
        "markdown_layout_context": True,
        "is_active": True,
        "latest_version": 2,
    }
    template.update(changes)
    return template


class RelayTemplateEditorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@example.com", password="test-pass", is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    @patch("studio.views.email_templates.configured_template_client")
    def test_list_uses_relay_catalog_and_marks_raw_template_read_only(self, configured):
        configured.return_value.templates.return_value = (
            relay_template(),
            relay_template("raw_transactional", name="Raw compatibility template"),
            relay_template("relay-inbound-forward", name="Internal inbound template"),
        )

        response = self.client.get(reverse("studio_email_template_list"))

        self.assertContains(response, "Welcome")
        self.assertContains(response, "raw_transactional")
        self.assertContains(response, "Compatibility template")
        self.assertNotContains(response, "relay-inbound-forward")
        self.assertContains(response, "Sends use v1", count=2)

    @patch("studio.views.email_templates.configured_template_client")
    def test_save_updates_relay_draft_without_publishing(self, configured):
        relay = configured.return_value
        relay.template.return_value = relay_template()
        relay.save_draft.return_value = relay_template(subject="Updated")
        relay.versions.return_value = ({"version": 2},)

        response = self.client.post(
            reverse("studio_email_template_edit", args=["welcome"]),
            {
                "name": "Welcome",
                "description": "Updated description",
                "subject": "Updated",
                "markdown_body": "Updated body",
                "category": "transactional",
                "default_sender_id": "transactional",
                "is_active": "on",
                "markdown_layout_context": "on",
            },
        )

        self.assertContains(response, "Updated")
        draft = relay.save_draft.call_args.args[1]
        self.assertEqual(draft["subject"], "Updated")
        self.assertEqual(draft["required_context"], ["user_name"])
        self.assertTrue(draft["markdown_layout_context"])
        relay.publish.assert_not_called()

    @patch("studio.views.email_templates.configured_template_client")
    def test_publish_is_explicit(self, configured):
        configured.return_value.publish.return_value = 3

        response = self.client.post(
            reverse("studio_email_template_publish", args=["welcome"])
        )

        self.assertRedirects(
            response,
            reverse("studio_email_template_edit", args=["welcome"]),
            fetch_redirect_response=False,
        )
        configured.return_value.publish.assert_called_once_with("welcome")

    @patch("studio.views.email_templates.configured_template_client")
    def test_editor_distinguishes_latest_from_active_send_version(self, configured):
        configured.return_value.template.return_value = relay_template(latest_version=3)
        configured.return_value.versions.return_value = ({"version": 3},)

        response = self.client.get(
            reverse("studio_email_template_edit", args=["welcome"]),
        )

        self.assertContains(response, "Website sends currently use immutable Relay v1")

    @patch(
        "studio.views.email_templates.get_preview_context",
        return_value={"user_name": "Ada"},
    )
    @patch("studio.views.email_templates.configured_template_client")
    def test_dotted_key_draft_preview_uses_relay_contract(self, configured, _context):
        configured.return_value.preview.return_value = {
            "subject": "Ready",
            "html_body": "<p>Relay draft</p>",
            "text_body": "Relay draft",
        }

        response = self.client.post(
            reverse(
                "studio_email_template_preview",
                args=["coursework.pool_ready"],
            ),
            {"recipient": "named", "version": ""},
        )

        self.assertEqual(response.content, b"<p>Relay draft</p>")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        call = configured.return_value.preview.call_args
        self.assertEqual(call.args[0], "coursework.pool_ready")
        self.assertEqual(call.kwargs, {"version": None, "draft": True})

    @patch("studio.views.email_templates.configured_template_client")
    def test_relay_failure_has_no_local_fallback(self, configured):
        configured.side_effect = RelayTemplateError("relay_unavailable")

        response = self.client.get(reverse("studio_email_template_list"))

        self.assertContains(response, "relay_unavailable")

    @patch("studio.views.email_templates.configured_template_client")
    def test_internal_and_read_only_templates_cannot_be_mutated(self, configured):
        for template_name in ("raw_transactional", "relay-inbound-forward"):
            for route in (
                "studio_email_template_edit",
                "studio_email_template_publish",
            ):
                with self.subTest(template_name=template_name, route=route):
                    response = self.client.post(reverse(route, args=[template_name]))
                    self.assertEqual(response.status_code, 404)
        configured.assert_not_called()

    @patch("studio.views.email_templates.configured_template_client")
    def test_internal_template_cannot_be_previewed(self, configured):
        response = self.client.post(
            reverse("studio_email_template_preview", args=["relay-inbound-forward"]),
        )

        self.assertEqual(response.status_code, 404)
        configured.assert_not_called()
