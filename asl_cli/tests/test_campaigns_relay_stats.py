import json
from unittest.mock import Mock, patch

from asl_cli.cli import cli
from click.testing import CliRunner

RELAY_STATS = {
    "sent_count": 200,
    "delivered_count": 198,
    "unique_open_count": 75,
    "open_count": 96,
    "unique_click_count": 12,
    "click_count": 18,
    "unsubscribe_count": 1,
    "bounce_count": 2,
    "complaint_count": 0,
}


def test_campaign_get_prints_relay_aggregate_stats():
    client = Mock()
    client.get.return_value = {"relay_stats": RELAY_STATS, "relay_stats_available": True}

    with patch("asl_cli.commands.campaigns.get_client", return_value=client):
        result = CliRunner().invoke(cli, ["campaigns", "get", "42"])

    assert result.exit_code == 0
    assert json.loads(result.output)["relay_stats"] == RELAY_STATS


def test_campaign_list_prints_relay_aggregate_stats():
    client = Mock()
    client.get.return_value = {"campaigns": [{"id": 42, "relay_stats": RELAY_STATS}]}

    with patch("asl_cli.commands.campaigns.get_client", return_value=client):
        result = CliRunner().invoke(cli, ["campaigns", "list"])

    assert result.exit_code == 0
    assert json.loads(result.output)["campaigns"][0]["relay_stats"] == RELAY_STATS
