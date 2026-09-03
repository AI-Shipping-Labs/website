"""Opt-in live Slack announcement integration target (issue #1480).

This package is not a hermetic Django unit-test module. The real
``post_slack_announcement`` contract lives in
``test_post_slack_announcement_real.py`` and posts to Slack when an
operator explicitly opts in.

Run it with::

    make test-live-slack-announcement

That target requires ``SLACK_BOT_TOKEN`` and
``SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID`` (the ``#integration-tests``
channel). Default Django, Playwright, and deploy gates do not collect or
execute this side effect.
"""
