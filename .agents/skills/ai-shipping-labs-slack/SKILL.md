---
name: ai-shipping-labs-slack
description: Use when asked to read AI Shipping Labs Slack — get a Slack user's email, resolve a Slack handle/display name/real name to an email, look up a Slack user by ID, read a Slack thread or channel, or answer "who posted in #channel". Read-only Slack operations (users + public messages); no CRM writes.
metadata:
  short-description: Read Slack users and public messages; resolve handles to emails
---

# Slack: read users and messages

Read-only access to the AI Shipping Labs Slack workspace: resolve people to emails and read public threads/channels. This skill is Slack-only. Writing CRM records is the `ai-shipping-labs-users` skill; the end-to-end thread->CRM recipe is `slack-thread-to-crm`.

## Credentials

`SLACK_BOT_TOKEN` lives in the repo `.env`. Read it inline at call time; NEVER print, paste, or commit it.

```bash
cd /home/alexey/git/ai-shipping-labs
SLACK_TOKEN=$(grep -E '^SLACK_BOT_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
```

Header: `Authorization: Bearer <token>`.

Scopes available: `users:read`, `users:read.email`, `channels:read`, `channels:history`, `chat:write`. Hard limits that follow:

- Public channels and public history only.
- User profiles and emails are readable.
- NO private channels: `groups:read` / `groups:history` are not granted. Passing `private_channel` to `conversations.list` returns `missing_scope`.

## Resolve a person to an email

### By Slack user ID (`users.info`)

```bash
curl -s -H "Authorization: Bearer $SLACK_TOKEN" "https://slack.com/api/users.info?user=$U" \
  | python3 -c "import sys,json;u=json.load(sys.stdin)['user']['profile'];print(u.get('real_name'),u.get('email'))"
```

### By display name / real name (`users.list`)

`users.info` needs an ID. When you only have a handle or a name from a screenshot, page the directory and match yourself. `users.list` returns up to `limit` members per page; follow `response_metadata.next_cursor` until it is empty. Match against `name` (handle), `profile.real_name`, and `profile.display_name`.

```python
import json, subprocess

TOKEN = subprocess.run(
    "grep -E '^SLACK_BOT_TOKEN=' .env | head -1 | cut -d= -f2- | tr -d '\"'\"'\"'\\r'",
    shell=True, cwd="/home/alexey/git/ai-shipping-labs",
    capture_output=True, text=True).stdout.strip()

needle = "ostrovnoy"
cursor = ""
while True:
    url = f"https://slack.com/api/users.list?limit=200&cursor={cursor}"
    out = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bearer {TOKEN}", url],
        capture_output=True, text=True).stdout
    data = json.loads(out)
    for m in data.get("members", []):
        p = m.get("profile", {})
        hay = " ".join(filter(None, [
            m.get("name"), p.get("real_name"), p.get("display_name")])).lower()
        if needle in hay:
            print(m["id"], m.get("name"), p.get("real_name"), p.get("email"))
    cursor = data.get("response_metadata", {}).get("next_cursor", "")
    if not cursor:
        break
```

This is exactly how `Ostrovnoy` resolved to `kkrotov.kir@gmail.com` (`real_name` "Kir Ostrovnoy", id `U0ATQL8MUUE`).

### Reverse direction (`users.lookupByEmail`)

When you already have an email and want the Slack user, use `users.lookupByEmail?email=...` instead of paging the whole directory.

## Find a channel

```bash
curl -s -H "Authorization: Bearer $SLACK_TOKEN" \
  "https://slack.com/api/conversations.list?types=public_channel&limit=1000" \
  | python3 -c "import sys,json;[print(c['id'],c['name']) for c in json.load(sys.stdin)['channels'] if 'community' in c['name'].lower()]"
```

The main channel is `#community` = `C0AFZSRAYQ4`. The bot must be a member of the channel (`is_member=True`) to read its history.

## Read a thread

Find the parent message in recent history, then pull its replies. Note the parent `ts` and `reply_count`.

```bash
curl -s -H "Authorization: Bearer $SLACK_TOKEN" \
  "https://slack.com/api/conversations.history?channel=$CH&limit=200"
```

```bash
curl -s -H "Authorization: Bearer $SLACK_TOKEN" \
  "https://slack.com/api/conversations.replies?channel=$CH&ts=$TS&limit=200"
```

`reply_count` includes the parent message. Read every reply yourself; do not regex the text for intent.

## Next steps

- Resolving Slack people to platform accounts and writing CRM records: `ai-shipping-labs-users`.
- The full thread->CRM recipe (read thread, classify intent, write notes + tags): `slack-thread-to-crm`.
