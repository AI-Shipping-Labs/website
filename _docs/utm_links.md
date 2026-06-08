# UTM links: a plain-language field guide

This is for anyone creating tracked links in Studio (Studio > UTM Campaigns).
You do not need to be a marketer. Pick where you are sharing the link and what
you are promoting, then fill in the fields using the conventions below.

A UTM link is just a normal link with a few extra `?utm_...` tags on the end.
Those tags tell our analytics where the click came from, so we can see which
channels and which campaigns actually bring people in.

## The five fields, in plain words

| Field | What it means | Example |
|---|---|---|
| `utm_source` | WHERE the click came from — the specific platform | `youtube`, `github`, `docs`, `linkedin`, `twitter`, `newsletter` |
| `utm_medium` | The TYPE of channel (not the platform) | `social`, `referral`, `email` |
| `utm_campaign` | The campaign the link belongs to | set once on the campaign (its slug) |
| `utm_content` | WHICH thing or placement you are promoting | `homepage`, `event_series`, `course_launch` |
| Destination | The page people land on | `/` (homepage), `/events`, a course URL, or a full URL |
| `utm_term` | Optional. A keyword or audience you want to track | usually left blank |

In Studio: `utm_campaign` comes from the campaign itself, and `utm_source` /
`utm_medium` default to the campaign's defaults. You set `utm_content` and the
destination per link, and you can override source/medium on a single link when
you need to.

## The one convention to remember

- `utm_source` = the platform (where it was posted)
- `utm_medium` = the kind of channel that platform is
- `utm_content` = the thing you are promoting

Create a SEPARATE tracked link for each (channel x thing) combination. That is
what lets you compare channels against each other AND campaigns against each
other.

### `utm_medium` is the channel TYPE, not the platform

This is the most common mistake. The platform name goes in `utm_source`. The
medium is the broad category of traffic.

| `utm_medium` | When to use it |
|---|---|
| `social` | LinkedIn, X/Twitter, YouTube, Reddit, Facebook posts |
| `referral` | Links from docs, GitHub READMEs, blog posts, partner sites |
| `email` | Newsletters and email campaigns |
| `cpc` | Paid ads (Google Ads, LinkedIn Ads) |
| `display` | Banner ads |
| `organic` | Unpaid promotion (many teams do not tag this by hand) |
| `community` | Optional custom: Slack, Discord, forums |

## Channel cheat-sheet (where it is shared)

Use the same mapping every time so reporting stays consistent. If GitHub is
`referral`, never tag it `social` or `github` somewhere else.

| Channel (where you post) | `utm_source` | `utm_medium` |
|---|---|---|
| YouTube video description | `youtube` | `social` |
| LinkedIn post | `linkedin` | `social` |
| X / Twitter post | `twitter` | `social` |
| Documentation site | `docs` | `referral` |
| GitHub README | `github` | `referral` |
| Newsletter / email | `newsletter` | `email` |

Consistency is the whole point. The values to standardize on:

- `utm_source`: `youtube`, `docs`, `github`, `linkedin`, `twitter`, `newsletter`
- `utm_medium`: `social`, `referral`, `email`
- `utm_content`: `homepage`, `event_series`, `course_launch`, etc.

## How the final URL is built

Studio assembles the URL for you from the fields above. A few things worth
knowing so the result is never a surprise:

- If the destination starts with `/` (a path), it is prefixed with the site
  base URL. A full `https://...` destination is used as-is.
- The UTM params are always added in the same order: `utm_source`,
  `utm_medium`, `utm_campaign`, `utm_content`, then `utm_term` if you set one.
- Any non-UTM query params and any `#fragment` already on the destination are
  kept.

So `/?utm_source=github...` on the destination `/` becomes
`https://aishippinglabs.com/?utm_source=github&utm_medium=referral&utm_campaign=...&utm_content=homepage`.

## Worked examples (copy the pattern)

### Example A — promote the homepage across channels

Goal: drive people to the community homepage. Destination: `/`.
`utm_content`: `homepage` on every link.

| Channel | `utm_source` | `utm_medium` | `utm_content` |
|---|---|---|---|
| YouTube | `youtube` | `social` | `homepage` |
| Docs | `docs` | `referral` | `homepage` |
| GitHub | `github` | `referral` | `homepage` |
| LinkedIn | `linkedin` | `social` | `homepage` |
| X / Twitter | `twitter` | `social` | `homepage` |

Resulting link for the GitHub README:

```
/?utm_source=github&utm_medium=referral&utm_campaign=<campaign>&utm_content=homepage
```

### Example B — promote an event series across channels

Goal: send people to the events page (or a specific series page) so signups
attribute to this campaign. Destination: `/events` (or the series URL).
`utm_content`: `event_series` on every link.

| Channel | `utm_source` | `utm_medium` | `utm_content` |
|---|---|---|---|
| YouTube | `youtube` | `social` | `event_series` |
| Docs | `docs` | `referral` | `event_series` |
| GitHub | `github` | `referral` | `event_series` |
| LinkedIn | `linkedin` | `social` | `event_series` |
| X / Twitter | `twitter` | `social` | `event_series` |

Resulting link for a YouTube video description:

```
/events?utm_source=youtube&utm_medium=social&utm_campaign=<campaign>&utm_content=event_series
```

### Example C — promote a course

Goal: send people to a course page for a launch. Destination: the course URL
(e.g. `/courses/llm-zoomcamp`). `utm_content`: `course_launch` (or
`course_<name>` if you run several at once).

| Channel | `utm_source` | `utm_medium` | `utm_content` |
|---|---|---|---|
| Newsletter | `newsletter` | `email` | `course_launch` |
| LinkedIn | `linkedin` | `social` | `course_launch` |
| GitHub README | `github` | `referral` | `course_launch` |

Resulting link for the newsletter:

```
/courses/llm-zoomcamp?utm_source=newsletter&utm_medium=email&utm_campaign=<campaign>&utm_content=course_launch
```

## Why this convention pays off

Following it produces clean, comparable reporting:

| Source | Content | Visits |
|---|---|---|
| `youtube` | `homepage` | 1,200 |
| `youtube` | `event_series` | 340 |
| `github` | `homepage` | 800 |

You can answer both "which channel performs best?" and "which campaign performs
best?" at a glance — which is only possible if everyone tags the same way.

## When to use the override fields

The form's "Override `utm_source`" / "Override `utm_medium`" fields are only
needed when a single link should differ from the campaign defaults. If you are
following the channel cheat-sheet above, set the right defaults on the campaign
and leave the overrides blank.
