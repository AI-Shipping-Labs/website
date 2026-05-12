"""Environment-aware Slack routing helpers."""

from integrations.config import get_config, is_enabled

SLACK_ENV_PRODUCTION = "production"
SLACK_ENV_DEVELOPMENT = "development"
SLACK_ENV_TEST = "test"
SLACK_ENVIRONMENTS = {
    SLACK_ENV_PRODUCTION,
    SLACK_ENV_DEVELOPMENT,
    SLACK_ENV_TEST,
}

ANNOUNCEMENTS_CHANNEL_KEYS = {
    SLACK_ENV_PRODUCTION: "SLACK_ANNOUNCEMENTS_CHANNEL_ID",
    SLACK_ENV_DEVELOPMENT: "SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID",
    SLACK_ENV_TEST: "SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID",
}

COMMUNITY_CHANNEL_KEYS = {
    SLACK_ENV_PRODUCTION: "SLACK_COMMUNITY_CHANNEL_IDS",
    SLACK_ENV_DEVELOPMENT: "SLACK_DEV_COMMUNITY_CHANNEL_IDS",
    SLACK_ENV_TEST: "SLACK_TEST_COMMUNITY_CHANNEL_IDS",
}

# Channel where "Ask the team to plan with me" pings land (issue #585).
TEAM_REQUESTS_CHANNEL_KEYS = {
    SLACK_ENV_PRODUCTION: "SLACK_TEAM_REQUESTS_CHANNEL_ID",
    SLACK_ENV_DEVELOPMENT: "SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID",
    SLACK_ENV_TEST: "SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID",
}


def get_slack_environment():
    """Return the configured Slack environment, defaulting safely to development."""
    value = str(get_config("SLACK_ENVIRONMENT", SLACK_ENV_DEVELOPMENT)).strip().lower()
    if value in SLACK_ENVIRONMENTS:
        return value
    return SLACK_ENV_DEVELOPMENT


def _csv_config(key):
    value = get_config(key, "")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def get_slack_announcements_channel_id():
    """Return the announcement channel for the active Slack environment."""
    key = ANNOUNCEMENTS_CHANNEL_KEYS[get_slack_environment()]
    return str(get_config(key, "")).strip()


def get_slack_community_channel_ids():
    """Return community channel IDs for the active Slack environment."""
    key = COMMUNITY_CHANNEL_KEYS[get_slack_environment()]
    return _csv_config(key)


def get_slack_team_requests_channel_id():
    """Return the team-requests channel for the active Slack environment.

    The team-requests channel is where "Ask the team to plan with me"
    pings (issue #585) are posted. Returns an empty string when no
    channel is configured for the active environment, in which case the
    caller falls back to email + in-app notifications.
    """
    key = TEAM_REQUESTS_CHANNEL_KEYS[get_slack_environment()]
    return str(get_config(key, "")).strip()


def slack_api_enabled():
    """Return true when Slack API calls may be attempted."""
    return is_enabled("SLACK_ENABLED") and bool(get_config("SLACK_BOT_TOKEN"))
