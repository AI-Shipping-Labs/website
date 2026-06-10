"""Slack implementation of the CommunityService.

Uses Slack Web API (channel-based approach - Approach B from spec 09):
- conversations.invite to add users to private community channels
- conversations.kick to remove users from community channels
- users.lookupByEmail to find Slack users by email

Requires a Slack bot token with scopes:
- users:read
- users:read.email
- channels:read
- chat:write

The bot must be a member of the community channels to use
conversations.invite and conversations.kick.
"""

import json
import logging

import requests
from django.core.mail import send_mail

from community.models import CommunityAuditLog
from community.services.base import CommunityService
from community.slack_config import get_slack_community_channel_ids
from integrations.config import get_config, is_enabled

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
        self.bot_token = bot_token or get_config('SLACK_BOT_TOKEN')
        if channel_ids is not None:
            self.channel_ids = channel_ids
        else:
            self.channel_ids = get_slack_community_channel_ids()

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
        if not is_enabled('SLACK_ENABLED'):
            raise SlackAPIError('Slack integration is disabled (SLACK_ENABLED is not true)')

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

    def fetch_conversation_history(self, channel_id, oldest=None, limit=200):
        """Fetch all top-level messages in a channel, following pagination.

        Wraps ``conversations.history`` (issue #889 inbound ingest). The
        bot must be a member of ``channel_id`` and the token needs
        ``channels:history`` (public) / ``groups:history`` (private).

        Args:
            channel_id: The Slack channel ID to read.
            oldest: Optional Slack ts; only messages at or after this ts
                are returned (inclusive=False — Slack excludes the
                boundary message, which is what we want since ``oldest``
                is the last run's ``latest_ts``).
            limit: Page size (Slack caps at 1000; 200 is the default).

        Returns:
            list[dict]: Every top-level message across all pages, in the
            order Slack returns them (newest-first within each page).

        Raises:
            SlackAPIError: If any API call fails.
        """
        messages = []
        cursor = None
        while True:
            params = {"channel": channel_id, "limit": limit}
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor
            data = self._api_call("conversations.history", **params)
            messages.extend(data.get("messages", []) or [])
            cursor = (data.get("response_metadata", {}) or {}).get("next_cursor", "")
            if not cursor:
                break
        return messages

    def fetch_conversation_replies(self, channel_id, thread_ts, limit=200):
        """Fetch every message in a thread (root + all replies), paginated.

        Wraps ``conversations.replies`` (issue #889). The first message
        Slack returns is the thread root; the rest are replies in
        chronological order.

        Args:
            channel_id: The Slack channel ID.
            thread_ts: The thread root ts.
            limit: Page size.

        Returns:
            list[dict]: Root message followed by every reply, across all
            pages.

        Raises:
            SlackAPIError: If any API call fails.
        """
        messages = []
        cursor = None
        while True:
            params = {"channel": channel_id, "ts": thread_ts, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = self._api_call("conversations.replies", **params)
            messages.extend(data.get("messages", []) or [])
            cursor = (data.get("response_metadata", {}) or {}).get("next_cursor", "")
            if not cursor:
                break
        return messages

    def lookup_user_display_name(self, slack_user_id):
        """Best-effort resolve a Slack user's display name via ``users.info``.

        Returns an empty string (never raises) on any failure so the
        ingest path can degrade gracefully — a blank display name is
        acceptable per the issue #889 spec.
        """
        if not slack_user_id:
            return ""
        try:
            data = self._api_call("users.info", user=slack_user_id)
        except SlackAPIError:
            return ""
        user = data.get("user", {}) or {}
        profile = user.get("profile", {}) or {}
        return (
            profile.get("display_name")
            or user.get("real_name")
            or profile.get("real_name")
            or ""
        )

    def get_message_permalink(self, channel_id, message_ts):
        """Best-effort Slack permalink for a message via ``chat.getPermalink``.

        Returns an empty string (never raises) on failure — a blank
        permalink is acceptable per the issue #889 spec.
        """
        try:
            data = self._api_call(
                "chat.getPermalink", channel=channel_id, message_ts=message_ts,
            )
        except SlackAPIError:
            return ""
        return data.get("permalink", "") or ""

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

    def lookup_user_profile_by_email(self, email):
        """Look up a Slack user's profile (id + name fields) by email.

        Companion to ``lookup_user_by_email`` that surfaces the name
        fields ``users.lookupByEmail`` already returns. Used by the
        Slack membership probe (issue #699) to backfill
        ``first_name`` / ``last_name`` on the local user without
        adding a second API surface.

        Args:
            email: Email address to search for.

        Returns:
            dict or None: ``{"id", "real_name", "first_name",
            "last_name"}`` when the user is found, ``None`` if the
            user is not in the workspace. Raises ``SlackAPIError`` for
            any other failure so callers can decide whether to swallow.
        """
        try:
            data = self._api_call("users.lookupByEmail", email=email)
        except SlackAPIError as e:
            if e.error_code == "users_not_found":
                return None
            raise

        user = data.get("user", {}) or {}
        if not user.get("id"):
            return None
        profile = user.get("profile", {}) or {}
        return {
            "id": user.get("id", ""),
            "real_name": user.get("real_name", "") or profile.get("real_name", ""),
            "first_name": profile.get("first_name", "") or "",
            "last_name": profile.get("last_name", "") or "",
        }

    def check_workspace_membership(self, email):
        """Check whether an email is a member of the Slack workspace.

        Three-state result. ``unknown`` means the call failed transiently
        (rate limit, 5xx, network error) and the caller should leave the
        existing state alone — we'll retry on the next cycle.

        Also returns ``unknown`` when the integration isn't configured
        (no bot token, ``SLACK_ENABLED`` off) so local dev never crashes.

        Args:
            email: Email address to check.

        Returns:
            tuple[str, str|None]: One of:
                - ("member", slack_user_id) — found in workspace.
                - ("not_member", None) — Slack returned users_not_found.
                - ("unknown", None) — transient failure or not configured.
        """
        # Bail early if not configured. is_enabled() check is also done
        # inside _api_call, but checking here lets us return "unknown"
        # instead of bubbling up a SlackAPIError on first call in dev.
        if not self.bot_token or not is_enabled('SLACK_ENABLED'):
            return ("unknown", None)

        try:
            data = self._api_call("users.lookupByEmail", email=email)
            uid = data.get("user", {}).get("id")
            if uid:
                return ("member", uid)
            # ok=True but no user.id — treat as unknown so we retry.
            return ("unknown", None)
        except SlackAPIError as e:
            if e.error_code == "users_not_found":
                return ("not_member", None)
            # ratelimited, fatal_error, internal_error, service_unavailable,
            # any other Slack-side or HTTP-level failure: be conservative.
            logger.warning(
                "Slack workspace membership check failed for %s: %s",
                email, e,
            )
            return ("unknown", None)
        except requests.RequestException as e:
            # Network error, timeout, DNS, etc.
            logger.warning(
                "Slack workspace membership check network error for %s: %s",
                email, e,
            )
            return ("unknown", None)

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
        slack_invite_url = get_config('SLACK_INVITE_URL')
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
