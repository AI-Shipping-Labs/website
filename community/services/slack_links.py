"""Shared helpers for building Slack deep-link URLs.

Extracted from ``studio.views.users`` in issue #700 so the same builder
can power Studio's user-detail page and the member-facing /account/
Slack card without two copies drifting apart.

The canonical form is the web URL ``https://app.slack.com/client/<team>/<user>``
which Slack desktop and mobile both deep-link from. See the issue for
why we deliberately do not introduce the ``slack://`` scheme.
"""


def build_slack_profile_url(slack_user_id, slack_team_id):
    """Return the canonical web URL for a member's Slack profile, or ''.

    Mirrors the Stripe pattern: requires BOTH the per-user ID and the
    workspace team ID. When either is empty we return the empty string so
    template callers can branch on truthiness alone.
    """
    if not slack_user_id or not slack_team_id:
        return ''
    return f'https://app.slack.com/client/{slack_team_id}/{slack_user_id}'
