"""Runtime config helpers for the Book Club (issue #1362).

The community "#book-club on Slack" link is a community-wide value, not a
per-Book field. It is read through the IntegrationSetting framework
(``integrations.config.get_config``) so it is editable from Studio settings
with no redeploy, and registered in ``integrations/settings_registry.py``
(slack group). Never hardcode a ``#`` anchor or read a raw setting.
"""

from integrations.config import get_config

# Fallback path when the operator has not set a Slack URL. Points at the
# account page, where Slack joining lives, rather than a dead ``#`` anchor.
BOOK_CLUB_SLACK_URL_DEFAULT = '/account'


def get_book_club_slack_url():
    """Return the community #book-club Slack URL (DB override -> env -> default)."""
    return get_config('BOOK_CLUB_SLACK_URL', BOOK_CLUB_SLACK_URL_DEFAULT)
