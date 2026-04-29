"""Shared request parsing helpers for payment views."""

import json

from django.http import JsonResponse

BILLING_PERIODS = ('monthly', 'yearly')


def parse_json_body(request):
    """Return ``(data, response)`` for a JSON request body."""
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({"error": "Invalid JSON"}, status=400)


def extract_tier_billing_payload(data, *, validate_billing_period=False):
    """Extract tier and billing fields shared by checkout subscription views."""
    tier_slug = data.get("tier_slug", "")
    billing_period = data.get("billing_period", "monthly")

    if not tier_slug:
        return tier_slug, billing_period, JsonResponse(
            {"error": "tier_slug is required"}, status=400
        )

    if validate_billing_period and billing_period not in BILLING_PERIODS:
        return tier_slug, billing_period, JsonResponse(
            {"error": "billing_period must be 'monthly' or 'yearly'"}, status=400
        )

    return tier_slug, billing_period, None
