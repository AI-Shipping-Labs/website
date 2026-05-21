"""Map an HTTP `Referer` hostname to a normalized acquisition channel.

Used by `analytics.middleware.CampaignTrackingMiddleware` to bucket external
referrers into a small, stable set of source labels (`linkedin`, `youtube`,
`chatgpt`, `google`, …) so we can attribute organic signups without UTM tags.

The bucket list is intentionally short and curated — adding a new bucket is
a code change and a migration is not required (the field is a free CharField
with `TextChoices` for the admin dropdown). New buckets must be added in
most-specific-first order: `gemini.google.com` must resolve to `gemini`
before the `google.com` rule catches it.
"""

import re

from django.db import models


class ReferrerSource(models.TextChoices):
    """Normalized acquisition channels.

    Stored on `UserAttribution.{first,last}_touch_referrer_source`. The
    enum drives the admin filter dropdown automatically. Empty string is
    used as the pre-feature sentinel — rows created before this enum
    existed have an empty `_source` value, distinguishable from `DIRECT`
    (which means "user landed without a Referer header").
    """

    LINKEDIN = 'linkedin', 'LinkedIn'
    YOUTUBE = 'youtube', 'YouTube'
    CHATGPT = 'chatgpt', 'ChatGPT'
    PERPLEXITY = 'perplexity', 'Perplexity'
    CLAUDE = 'claude', 'Claude'
    GEMINI = 'gemini', 'Gemini'
    GOOGLE = 'google', 'Google'
    BING = 'bing', 'Bing'
    DUCKDUCKGO = 'duckduckgo', 'DuckDuckGo'
    TWITTER = 'twitter', 'Twitter / X'
    FACEBOOK = 'facebook', 'Facebook'
    REDDIT = 'reddit', 'Reddit'
    HACKERNEWS = 'hackernews', 'Hacker News'
    GITHUB = 'github', 'GitHub'
    MEDIUM = 'medium', 'Medium'
    SUBSTACK = 'substack', 'Substack'
    INTERNAL = 'internal', 'Internal (aishippinglabs.com)'
    OTHER = 'other', 'Other'
    DIRECT = 'direct', 'Direct (no referrer)'


# Hostname suffix → normalized source. Order matters: most specific first.
# `endswith` on the lowercased hostname is the matcher. Subdomains like
# `someone.medium.com` match `medium.com`; `gemini.google.com` is listed
# above `google.com` so it does not collapse into the search bucket.
_HOST_RULES = (
    # AI assistants and chat (must come before `google.com`)
    ('chat.openai.com', ReferrerSource.CHATGPT),
    ('chatgpt.com', ReferrerSource.CHATGPT),
    ('perplexity.ai', ReferrerSource.PERPLEXITY),
    ('claude.ai', ReferrerSource.CLAUDE),
    ('gemini.google.com', ReferrerSource.GEMINI),
    ('bard.google.com', ReferrerSource.GEMINI),

    # Social
    ('linkedin.com', ReferrerSource.LINKEDIN),
    ('lnkd.in', ReferrerSource.LINKEDIN),
    ('youtube.com', ReferrerSource.YOUTUBE),
    ('youtu.be', ReferrerSource.YOUTUBE),
    ('twitter.com', ReferrerSource.TWITTER),
    ('x.com', ReferrerSource.TWITTER),
    ('t.co', ReferrerSource.TWITTER),
    ('facebook.com', ReferrerSource.FACEBOOK),
    ('m.facebook.com', ReferrerSource.FACEBOOK),
    ('fb.me', ReferrerSource.FACEBOOK),
    ('reddit.com', ReferrerSource.REDDIT),
    ('old.reddit.com', ReferrerSource.REDDIT),
    ('news.ycombinator.com', ReferrerSource.HACKERNEWS),
    ('github.com', ReferrerSource.GITHUB),
    ('medium.com', ReferrerSource.MEDIUM),
    ('substack.com', ReferrerSource.SUBSTACK),

    # Search
    ('bing.com', ReferrerSource.BING),
    ('duckduckgo.com', ReferrerSource.DUCKDUCKGO),

    # Internal
    ('aishippinglabs.com', ReferrerSource.INTERNAL),
)

# Google has many cc-TLDs (`google.de`, `google.co.uk`, …). One regex
# handles them all. Must run AFTER `gemini.google.com` / `bard.google.com`
# so those resolve to GEMINI first.
_GOOGLE_RE = re.compile(r'(^|\.)google\.[a-z][a-z.]+$')


def normalize_referrer(host: str) -> str:
    """Return the normalized bucket string for a referrer hostname.

    `host` should already be lowercased and stripped (caller's job — the
    middleware parses it via `urlparse(...).hostname`). Returns one of
    `ReferrerSource.values`:

    - `''` → `direct` (no referrer header at all)
    - matches a known suffix → that bucket (`linkedin`, `youtube`, …)
    - non-empty unknown → `other`
    """
    if not host:
        return ReferrerSource.DIRECT.value

    for suffix, source in _HOST_RULES:
        if host == suffix or host.endswith('.' + suffix):
            return source.value

    if _GOOGLE_RE.search(host):
        return ReferrerSource.GOOGLE.value

    return ReferrerSource.OTHER.value


__all__ = ['ReferrerSource', 'normalize_referrer']
