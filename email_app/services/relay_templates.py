"""Fail-closed Relay template catalog boundary for AISL Studio."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from email_app.relay_versions import template_version_for
from integrations.config import get_config

RELAY_TIMEOUT_SECONDS = 15
INTERNAL_TEMPLATE_KEYS = frozenset({"relay-inbound-forward"})
READ_ONLY_TEMPLATE_KEYS = frozenset({"raw_transactional"})


class RelayTemplateError(RuntimeError):
    """Safe template API error containing no draft or recipient content."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RelayTemplateClient:
    base_url: str
    api_key: str

    def templates(self):
        document = self._request("GET", "/api/transactional/templates", expected={200})
        rows = document.get("templates")
        if not isinstance(rows, list):
            raise RelayTemplateError("malformed_templates_response")
        return tuple(row for row in rows if _visible_template(row))

    def template(self, template_key):
        document = self._request(
            "GET", f"/api/transactional/templates/{template_key}", expected={200}
        )
        return _template(document, template_key)

    def save_draft(self, template_key, draft):
        document = self._request(
            "PUT", f"/api/transactional/templates/{template_key}", draft, expected={200, 201}
        )
        return _template(document.get("template"), template_key)

    def publish(self, template_key):
        document = self._request(
            "POST", f"/api/transactional/templates/{template_key}/publish", {}, expected={200, 201}
        )
        version = document.get("version")
        if not isinstance(version, dict):
            raise RelayTemplateError("malformed_publish_response")
        number = version.get("version")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise RelayTemplateError("malformed_publish_response")
        return number

    def versions(self, template_key):
        document = self._request(
            "GET", f"/api/transactional/templates/{template_key}/versions", expected={200}
        )
        rows = document.get("versions")
        if not isinstance(rows, list):
            raise RelayTemplateError("malformed_versions_response")
        return tuple(rows)

    def preview(self, template_key, context, *, version=None, draft=False):
        payload = {"context": context}
        if version is not None:
            payload["template_version"] = version
        if draft:
            payload["draft"] = True
        document = self._request(
            "POST", f"/api/transactional/templates/{template_key}/preview", payload, expected={200}
        )
        return _preview(document)

    def _request(self, method, path, payload=None, *, expected):
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = requests.request(
                method, f"{self.base_url}{path}", json=payload,
                headers=headers, timeout=RELAY_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise RelayTemplateError("relay_unavailable") from error
        if response.status_code not in expected:
            raise RelayTemplateError(f"relay_http_{response.status_code}")
        try:
            document = response.json()
        except (TypeError, ValueError) as error:
            raise RelayTemplateError("malformed_relay_response") from error
        if not isinstance(document, dict):
            raise RelayTemplateError("malformed_relay_response")
        return document


def configured_template_client():
    base_url = str(get_config("RELAY_BASE_URL", "") or "").strip().rstrip("/")
    api_key = str(get_config("RELAY_API_KEY", "") or "").strip()
    if not base_url or not api_key:
        raise RelayTemplateError("relay_not_configured")
    return RelayTemplateClient(base_url, api_key)


def relay_template_subject(template_key, context):
    """Render the audit subject through the pinned Relay template version."""
    rendered = configured_template_client().preview(
        template_key,
        context,
        version=template_version_for(template_key),
    )
    return rendered["subject"]


def _visible_template(row):
    return isinstance(row, dict) and row.get("key") not in INTERNAL_TEMPLATE_KEYS


def _template(document, template_key):
    if not isinstance(document, dict) or document.get("key") != template_key:
        raise RelayTemplateError("malformed_template_response")
    return document


def _preview(document):
    fields = ("subject", "html_body", "text_body")
    if any(not isinstance(document.get(field), str) for field in fields):
        raise RelayTemplateError("malformed_preview_response")
    return document
