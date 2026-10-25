from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from email_app.relay_versions import RELAY_TEMPLATE_KEYS, template_version_for
from email_app.services.relay_templates import (
    RelayTemplateClient,
    RelayTemplateError,
)


class RelayTemplateClientTest(SimpleTestCase):
    def setUp(self):
        self.client = RelayTemplateClient("https://relay.example", "secret")

    @patch("email_app.services.relay_templates.requests.request")
    def test_dotted_template_get_uses_relay_route(self, request):
        request.return_value = Mock(
            status_code=200,
            json=lambda: {"key": "coursework.pool_ready", "subject": "Ready"},
        )

        result = self.client.template("coursework.pool_ready")

        self.assertEqual(result["subject"], "Ready")
        self.assertEqual(
            request.call_args.args,
            ("GET", "https://relay.example/api/transactional/templates/coursework.pool_ready"),
        )

    @patch("email_app.services.relay_templates.requests.request")
    def test_draft_preview_sets_explicit_draft_flag(self, request):
        request.return_value = Mock(
            status_code=200,
            json=lambda: {
                "subject": "Draft", "html_body": "<p>Draft</p>",
                "text_body": "Draft",
            },
        )

        rendered = self.client.preview(
            "welcome", {"user_name": "Ada"}, draft=True,
        )

        self.assertEqual(rendered["subject"], "Draft")
        self.assertEqual(
            request.call_args.kwargs["json"],
            {"context": {"user_name": "Ada"}, "draft": True},
        )

    @patch("email_app.services.relay_templates.requests.request")
    def test_publish_reads_nested_immutable_version(self, request):
        request.return_value = Mock(
            status_code=201,
            json=lambda: {
                "template_key": "welcome",
                "latest_version": 2,
                "version": {"template_key": "welcome", "version": 2},
            },
        )

        self.assertEqual(self.client.publish("welcome"), 2)

    @patch("email_app.services.relay_templates.requests.request")
    def test_malformed_response_fails_closed(self, request):
        request.return_value = Mock(status_code=200, json=lambda: {"templates": {}})

        with self.assertRaisesMessage(
            RelayTemplateError, "malformed_templates_response"
        ):
            self.client.templates()


class RelayTemplateVersionPolicyTest(SimpleTestCase):
    def test_named_templates_use_v1_until_relay_v2_is_published(self):
        self.assertEqual(len(RELAY_TEMPLATE_KEYS), 61)
        for template_key in RELAY_TEMPLATE_KEYS:
            with self.subTest(template_key=template_key):
                self.assertEqual(template_version_for(template_key), 1)
        self.assertEqual(template_version_for("campaign-broadcast"), 2)

    def test_compatibility_and_unknown_templates_stay_version_one(self):
        self.assertEqual(template_version_for("raw_transactional"), 1)
        self.assertEqual(template_version_for("relay-inbound-forward"), 1)
        self.assertEqual(template_version_for("future-template"), 1)
