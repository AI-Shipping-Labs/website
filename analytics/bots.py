"""Bot User-Agent detection for analytics filtering."""

import re

# Case-insensitive regex matching common bot User-Agent strings.
# This is heuristic, not airtight — it's intended to filter the bulk of
# crawlers, scrapers, and headless test agents from campaign analytics.
BOT_UA_RE = re.compile(
    r'(bot|crawler|spider|slurp|bingpreview|facebookexternalhit|'
    r'twitterbot|linkedinbot|slackbot|discordbot|telegrambot|'
    r'whatsapp|preview|monitor|uptime|pingdom|googleother|'
    r'headlesschrome|phantomjs|puppeteer|playwright|cypress|httpclient|'
    r'curl|wget|python-requests|node-fetch|go-http-client)',
    re.IGNORECASE,
)


def is_bot(user_agent):
    """Return True if the user-agent string matches a known bot/crawler pattern.

    An empty / missing user-agent is NOT treated as a bot here so that we don't
    drop legitimate visitors whose client doesn't send a UA. Real bots almost
    always identify themselves; the regex catches them.
    """
    if not user_agent:
        return False
    return bool(BOT_UA_RE.search(user_agent))
