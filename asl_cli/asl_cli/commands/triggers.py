"""``asl triggers`` -- event triggers: subscriptions, widgets, emissions."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"


@click.group()
def triggers():
    """Manage event triggers."""


@triggers.group("subscriptions")
def trig_subscriptions():
    """Trigger subscriptions."""


@trig_subscriptions.command("list")
@format_option
def trigger_subscriptions(fmt):
    """List trigger subscriptions."""
    emit(get_client().get(f"{API}/triggers/subscriptions"), fmt)


@trig_subscriptions.command("get")
@click.argument("subscription_id", type=int)
@format_option
def trigger_subscription_get(subscription_id, fmt):
    """Get a single trigger subscription."""
    emit(get_client().get(f"{API}/triggers/subscriptions/{subscription_id}"), fmt)


@trig_subscriptions.command("update")
@click.argument("subscription_id", type=int)
@json_option("data", required=True)
@format_option
def trigger_subscription_update(subscription_id, data, fmt):
    """Update a trigger subscription."""
    emit(get_client().patch(f"{API}/triggers/subscriptions/{subscription_id}", json_body=data), fmt)


@triggers.group("widgets")
def trig_widgets():
    """Trigger widgets."""


@trig_widgets.command("list")
@format_option
def trigger_widgets(fmt):
    """List trigger widgets."""
    emit(get_client().get(f"{API}/triggers/widgets"), fmt)


@trig_widgets.command("get")
@click.argument("widget_id", type=int)
@format_option
def trigger_widget_get(widget_id, fmt):
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
