"""Abstract interface for community platform integration.

The CommunityService defines the contract that any community platform
(Slack, Discord, etc.) must implement. Callers use this interface so
the underlying platform can be swapped without changing business logic.
"""

import abc


class CommunityService(abc.ABC):
    """Abstract base class for community platform services.

    Implementations handle the platform-specific API calls for managing
    user access to community channels/spaces.
    """

    @abc.abstractmethod
    def invite(self, user, send_invite_email=True):
        """Invite a user to the community.

        If the user has a linked platform account (e.g. slack_user_id),
        adds them to community channels. If not, sends an invite email
        with instructions to join.

        Args:
            user: User model instance.
            send_invite_email: When False, a user who is not in the
                community platform is NOT emailed the generic invite,
                because the caller delivers the join link itself (the
                Maven enrollment flow puts it in ``maven_welcome``, so a
                cold enrollee receives exactly one email).

        Returns:
            InviteResult: ``outcome`` names what actually happened —
            added to channels, no channel joined, invite email sent,
            invite email failed, invite email suppressed by the caller,
            or invite email skipped by delivery policy — with a
            PII-free ``detail`` for the failure outcomes. Implementations
            must never report success for a step that did not occur: a
            resolved platform user id is not proof that the member joined
            anything (issue #1565).
        """

    @abc.abstractmethod
    def remove(self, user):
        """Remove a user from the community channels.

        Args:
            user: User model instance.
        """

    @abc.abstractmethod
    def reactivate(self, user):
        """Re-add a user to the community after a previous removal.

        If the user has a linked platform account, re-adds them to
        channels. If not, follows the same flow as invite.

        Args:
            user: User model instance.
        """

    @abc.abstractmethod
    def lookup_user_by_email(self, email):
        """Look up a community platform user by email.

        Args:
            email: The email address to search for.

        Returns:
            str or None: The platform user ID if found, None otherwise.
        """

    @abc.abstractmethod
    def add_to_channels(self, platform_user_id):
        """Add a user to all community channels.

        Args:
            platform_user_id: The platform-specific user ID.

        Returns:
            list[dict]: Results for each channel operation.
        """

    @abc.abstractmethod
    def remove_from_channels(self, platform_user_id):
        """Remove a user from all community channels.

        Args:
            platform_user_id: The platform-specific user ID.

        Returns:
            list[dict]: Results for each channel operation.
        """
