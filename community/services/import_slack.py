"""Slack workspace import adapter for the shared user-import runner."""

from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import IMPORT_SOURCE_SLACK
from accounts.services.import_users import ImportRow, register_import_adapter
from community.services.slack import SlackAPIError, SlackCommunityService
from community.slack_config import slack_api_enabled
from integrations.config import get_config, is_enabled

SLACK_USERS_LIST_LIMIT = 200
SLACK_CONFIGURATION_ERRORS = {
    "invalid_auth",
    "not_authed",
    "account_inactive",
    "token_revoked",
    "missing_scope",
    "invalid_arguments",
}


def slack_workspace_import_adapter():
    """Yield import rows for real Slack workspace members with visible emails."""
    _validate_slack_configuration()
    checked_at = timezone.now()
    service = SlackCommunityService()

    try:
        for member in _iter_slack_members(service):
            row = _row_for_member(member, checked_at=checked_at)
            if row is not None:
                yield row
    except SlackAPIError as exc:
        if exc.error_code in SLACK_CONFIGURATION_ERRORS or exc.method == "users.list":
            raise CommandError(f"Slack import configuration error: {exc}") from exc
        raise


def register_slack_import_adapter():
    """Register the Slack adapter with the shared import registry."""
    register_import_adapter(IMPORT_SOURCE_SLACK, slack_workspace_import_adapter)


def _validate_slack_configuration():
    if not is_enabled("SLACK_ENABLED"):
        raise CommandError("Slack import requires SLACK_ENABLED=true.")
    if not get_config("SLACK_BOT_TOKEN"):
        raise CommandError("Slack import requires SLACK_BOT_TOKEN.")
    if not slack_api_enabled():
        raise CommandError("Slack import is not configured.")


def _iter_slack_members(service):
    cursor = ""
    while True:
        response = service._api_call(
            "users.list",
            limit=SLACK_USERS_LIST_LIMIT,
            cursor=cursor,
        )
        yield from response.get("members") or []
        cursor = (
            response.get("response_metadata", {}).get("next_cursor") or ""
        ).strip()
        if not cursor:
            break


def _row_for_member(member, *, checked_at):
    if _skip_member(member):
        return None

    profile = member.get("profile") or {}
    email = (profile.get("email") or "").strip()
    slack_id = member.get("id") or ""
    name = _profile_name(profile)
    if not email:
        return ImportRow(
            email="",
            name=name,
            validation_error="Slack member has no visible profile email.",
            diagnostics={
                "kind": "missing_email",
                "slack_id": slack_id,
                "name": name,
                "message": (
                    "Slack member has no visible profile email; retry after Slack "
                    "email permissions change."
                ),
            },
        )

    is_admin = bool(member.get("is_admin"))
    is_owner = bool(member.get("is_owner"))
    is_guest = bool(member.get("is_restricted") or member.get("is_ultra_restricted"))
    tags = ["slack-member"]
    if is_admin or is_owner:
        tags.append("slack-admin")
    if is_guest:
        tags.append("slack-guest")

    return ImportRow(
        email=email,
        name=name,
        source_metadata=_source_metadata(member, profile),
        tags=tags,
        extra_user_fields={
            "slack_user_id": slack_id,
            "slack_member": True,
            "slack_checked_at": checked_at,
        },
    )


def _skip_member(member):
    if member.get("id") == "USLACKBOT" or member.get("name") == "slackbot":
        return True
    if member.get("deleted"):
        return True
    if member.get("is_bot") or member.get("is_app_user"):
        return True
    return bool(member.get("is_primary_owner"))


def _profile_name(profile):
    for key in (
        "real_name_normalized",
        "real_name",
        "display_name_normalized",
        "display_name",
    ):
        value = (profile.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_metadata(member, profile):
    metadata = {
        "slack_id": member.get("id") or "",
        "slack_team_id": member.get("team_id") or member.get("team") or "",
        "is_admin": bool(member.get("is_admin")),
        "is_owner": bool(member.get("is_owner")),
        "is_restricted": bool(member.get("is_restricted")),
        "is_ultra_restricted": bool(member.get("is_ultra_restricted")),
        "tz": member.get("tz") or "",
        "real_name_normalized": profile.get("real_name_normalized") or "",
        "real_name": profile.get("real_name") or "",
        "display_name_normalized": profile.get("display_name_normalized") or "",
        "display_name": profile.get("display_name") or "",
    }
    return {key: value for key, value in metadata.items() if value not in ("", None)}
