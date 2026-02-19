"""Slack implementation of the CommunityService.

Uses Slack Web API (channel-based approach - Approach B from spec 09):
- conversations.invite to add users to private community channels
- conversations.kick to remove users from community channels
- users.lookupByEmail to find Slack users by email

Requires a Slack bot token with scopes:
- conversations:invite
- conversations:kick (technically conversations:write)
- users:read.email
- users:read
"""

import json
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

from community.models import CommunityAuditLog
from community.services.base import CommunityService

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api/"


class SlackAPIError(Exception):
    """Raised when a Slack API call fails."""

    def __init__(self, message, method=None, error_code=None):
        self.method = method
        self.error_code = error_code
        super().__init__(message)


class SlackCommunityService(CommunityService):
    """Slack-based community service using channel-based access control.

    Bot adds/removes users from private community channels listed in
    settings.SLACK_COMMUNITY_CHANNEL_IDS.
    """

    def __init__(self, bot_token=None, channel_ids=None):
        self.bot_token = bot_token or getattr(settings, "SLACK_BOT_TOKEN", "")
        self.channel_ids = channel_ids or getattr(
            settings, "SLACK_COMMUNITY_CHANNEL_IDS", []
        )

    def _api_call(self, method, **kwargs):
        """Make a Slack Web API call.

        Args:
            method: Slack API method name (e.g. 'conversations.invite').
            **kwargs: Parameters for the API call.

        Returns:
            dict: The parsed JSON response.

        Raises:
            SlackAPIError: If the API call fails or returns ok=False.
        """
        url = f"{SLACK_API_BASE}{method}"
        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        response = requests.post(url, json=kwargs, headers=headers, timeout=10)

        if response.status_code != 200:
            raise SlackAPIError(
                f"Slack API HTTP error: {response.status_code}",
                method=method,
            )

        data = response.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            raise SlackAPIError(
                f"Slack API error: {error}",
                method=method,
                error_code=error,
            )

        return data

    def lookup_user_by_email(self, email):
        """Look up a Slack user by email address.

        Args:
            email: Email address to search for.

        Returns:
            str or None: Slack user ID if found, None if not found.
        """
        try:
            data = self._api_call("users.lookupByEmail", email=email)
            return data.get("user", {}).get("id")
        except SlackAPIError as e:
            if e.error_code == "users_not_found":
                return None
            raise

    def add_to_channels(self, slack_user_id):
        """Add a Slack user to all community channels.

        Args:
            slack_user_id: The Slack user ID to add.

        Returns:
            list[dict]: Results for each channel, with 'channel', 'ok', 'error' keys.
        """
        results = []
        for channel_id in self.channel_ids:
            try:
                self._api_call(
                    "conversations.invite",
                    channel=channel_id,
                    users=slack_user_id,
                )
                results.append({"channel": channel_id, "ok": True})
            except SlackAPIError as e:
                # "already_in_channel" is not an error - user is already there
                if e.error_code == "already_in_channel":
                    results.append({"channel": channel_id, "ok": True, "already_in": True})
                else:
                    logger.warning(
                        "Failed to add user %s to channel %s: %s",
                        slack_user_id, channel_id, e,
                    )
                    results.append({
                        "channel": channel_id,
                        "ok": False,
                        "error": str(e),
                    })
        return results

    def remove_from_channels(self, slack_user_id):
        """Remove a Slack user from all community channels.

        Args:
            slack_user_id: The Slack user ID to remove.

        Returns:
            list[dict]: Results for each channel, with 'channel', 'ok', 'error' keys.
        """
        results = []
        for channel_id in self.channel_ids:
            try:
                self._api_call(
                    "conversations.kick",
                    channel=channel_id,
                    user=slack_user_id,
                )
                results.append({"channel": channel_id, "ok": True})
            except SlackAPIError as e:
                # "not_in_channel" means user is already removed
                if e.error_code == "not_in_channel":
                    results.append({"channel": channel_id, "ok": True, "not_in": True})
                else:
                    logger.warning(
                        "Failed to remove user %s from channel %s: %s",
                        slack_user_id, channel_id, e,
                    )
                    results.append({
                        "channel": channel_id,
                        "ok": False,
                        "error": str(e),
                    })
        return results

    def invite(self, user):
        """Invite a user to Slack community channels.

        If the user has a slack_user_id, adds them directly to channels.
        If not, looks them up by email. If found, stores slack_user_id
        and adds to channels. If not found, sends an invite email.

        Args:
            user: User model instance.
        """
        slack_user_id = user.slack_user_id

        # Try to look up by email if no slack_user_id
        if not slack_user_id:
            slack_user_id = self.lookup_user_by_email(user.email)
            if slack_user_id:
                user.slack_user_id = slack_user_id
                user.save(update_fields=["slack_user_id"])

        if slack_user_id:
            results = self.add_to_channels(slack_user_id)
            CommunityAuditLog.objects.create(
                user=user,
                action="invite",
                details=json.dumps({
                    "slack_user_id": slack_user_id,
                    "channels": results,
                }),
            )
            logger.info(
                "Invited user %s (slack=%s) to community channels",
                user.email, slack_user_id,
            )
        else:
            # User not found in Slack - send invite email
            self._send_invite_email(user)
            CommunityAuditLog.objects.create(
                user=user,
                action="invite",
                details=json.dumps({
                    "status": "email_sent",
                    "reason": "slack_user_not_found",
                }),
            )
            logger.info(
                "Sent Slack invite email to user %s (not found in Slack)",
                user.email,
            )

    def remove(self, user):
        """Remove a user from Slack community channels.

        Args:
            user: User model instance.
        """
        if not user.slack_user_id:
            CommunityAuditLog.objects.create(
                user=user,
                action="remove",
                details=json.dumps({
                    "status": "skipped",
                    "reason": "no_slack_user_id",
                }),
            )
            logger.info(
                "Skipped removal for user %s (no slack_user_id)", user.email,
            )
            return

        results = self.remove_from_channels(user.slack_user_id)
        CommunityAuditLog.objects.create(
            user=user,
            action="remove",
            details=json.dumps({
                "slack_user_id": user.slack_user_id,
                "channels": results,
            }),
        )
        logger.info(
            "Removed user %s (slack=%s) from community channels",
            user.email, user.slack_user_id,
        )

    def reactivate(self, user):
        """Re-add a user to Slack community channels.

        If the user has a slack_user_id, re-adds them directly.
        If not, follows the same flow as invite (lookup or email).

        Args:
            user: User model instance.
        """
        slack_user_id = user.slack_user_id

        if not slack_user_id:
            slack_user_id = self.lookup_user_by_email(user.email)
            if slack_user_id:
                user.slack_user_id = slack_user_id
                user.save(update_fields=["slack_user_id"])

        if slack_user_id:
            results = self.add_to_channels(slack_user_id)
            CommunityAuditLog.objects.create(
                user=user,
                action="reactivate",
                details=json.dumps({
                    "slack_user_id": slack_user_id,
                    "channels": results,
                }),
            )
            logger.info(
                "Reactivated user %s (slack=%s) in community channels",
                user.email, slack_user_id,
            )
        else:
            self._send_invite_email(user)
            CommunityAuditLog.objects.create(
                user=user,
                action="reactivate",
                details=json.dumps({
                    "status": "email_sent",
                    "reason": "slack_user_not_found",
                }),
            )
            logger.info(
                "Sent Slack invite email to user %s on reactivation",
                user.email,
            )

    def _send_invite_email(self, user):
        """Send an email with Slack workspace invite link.

        Args:
            user: User model instance.
        """
        slack_invite_url = getattr(settings, "SLACK_INVITE_URL", "")
        try:
            send_mail(
                subject="Welcome to AI Shipping Labs community!",
                message=(
                    f"Hi,\n\n"
                    f"Welcome to AI Shipping Labs! Your membership includes access "
                    f"to our Slack community.\n\n"
                    f"Join our Slack workspace here: {slack_invite_url}\n\n"
                    f"Once you join, our system will automatically detect your email "
                    f"and add you to the community channels.\n\n"
                    f"- AI Shipping Labs"
                ),
                from_email=None,  # Uses DEFAULT_FROM_EMAIL
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            logger.exception(
                "Failed to send Slack invite email to %s", user.email,
            )


def get_community_service():
    """Factory function to get the configured CommunityService instance.

    Returns the SlackCommunityService by default. Can be extended to
    return different implementations based on settings.

    Returns:
        CommunityService: The configured community service instance.
    """
    return SlackCommunityService()
