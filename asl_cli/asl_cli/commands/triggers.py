"""``asl triggers`` -- event triggers: subscriptions, widgets, emissions."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_arg

API = "/api"

commands = []


# -- subscriptions -----------------------------------------------------------

@click.command("trigger-subscriptions")
@format_option
def trigger_subscriptions(fmt):
    """List trigger subscriptions."""
    data = get_client().get(f"{API}/triggers/subscriptions")
    emit(data, fmt)


commands.append(trigger_subscriptions)


@click.command("trigger-subscription-get")
@click.argument("subscription_id", type=int)
@format_option
def trigger_subscription_get(subscription_id, fmt):
    """Get a single trigger subscription."""
    data = get_client().get(f"{API}/triggers/subscriptions/{subscription_id}")
    emit(data, fmt)


commands.append(trigger_subscription_get)


@click.command("trigger-subscription-update")
@click.argument("subscription_id", type=int)
@json_arg("data", required=True)
@format_option
def trigger_subscription_update(subscription_id, data, fmt):
    """Update a trigger subscription (JSON body)."""
    result = get_client().patch(f"{API}/triggers/subscriptions/{subscription_id}", json_body=data)
    emit(result, fmt)


commands.append(trigger_subscription_update)


# -- widgets -----------------------------------------------------------------

@click.command("trigger-widgets")
@format_option
def trigger_widgets(fmt):
    """List trigger widgets."""
    data = get_client().get(f"{API}/triggers/widgets")
    emit(data, fmt)


commands.append(trigger_widgets)


@click.command("trigger-widget-get")
@click.argument("widget_id", type=int)
@format_option
def trigger_widget_get(widget_id, fmt):
    """Get a single trigger widget."""
    data = get_client().get(f"{API}/triggers/widgets/{widget_id}")
    emit(data, fmt)


commands.append(trigger_widget_get)


# -- emissions / deliveries --------------------------------------------------

@click.command("trigger-emissions")
@format_option
def trigger_emissions(fmt):
    """List trigger emissions."""
    data = get_client().get(f"{API}/triggers/emissions")
    emit(data, fmt)


commands.append(trigger_emissions)


@click.command("trigger-deliveries")
@format_option
def trigger_deliveries(fmt):
    """List trigger deliveries."""
    data = get_client().get(f"{API}/triggers/deliveries")
    emit(data, fmt)


commands.append(trigger_deliveries)
