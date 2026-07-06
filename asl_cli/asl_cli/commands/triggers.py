"""``asl triggers`` -- event triggers."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"


@click.group()
def triggers():
    """Manage event triggers."""


@triggers.command("subscriptions")
@format_option
def trigger_subscriptions(fmt):
    """List trigger subscriptions."""
    emit(get_client().get(f"{API}/triggers/subscriptions"), fmt)


@triggers.command("subscription")
@click.argument("subscription_id", type=int)
@format_option
def trigger_subscription(subscription_id, fmt):
    """Get a single trigger subscription."""
    emit(get_client().get(f"{API}/triggers/subscriptions/{subscription_id}"), fmt)


@triggers.command("update-subscription")
@click.argument("subscription_id", type=int)
@json_option("data", required=True)
@format_option
def trigger_update_subscription(subscription_id, data, fmt):
    """Update a trigger subscription."""
    emit(get_client().patch(f"{API}/triggers/subscriptions/{subscription_id}", json_body=data), fmt)


@triggers.command("widgets")
@format_option
def trigger_widgets(fmt):
    """List trigger widgets."""
    emit(get_client().get(f"{API}/triggers/widgets"), fmt)


@triggers.command("widget")
@click.argument("widget_id", type=int)
@format_option
def trigger_widget(widget_id, fmt):
    """Get a single trigger widget."""
    emit(get_client().get(f"{API}/triggers/widgets/{widget_id}"), fmt)


@triggers.command("emissions")
@format_option
def trigger_emissions(fmt):
    """List trigger emissions."""
    emit(get_client().get(f"{API}/triggers/emissions"), fmt)


@triggers.command("deliveries")
@format_option
def trigger_deliveries(fmt):
    """List trigger deliveries."""
    emit(get_client().get(f"{API}/triggers/deliveries"), fmt)


groups = [triggers]
