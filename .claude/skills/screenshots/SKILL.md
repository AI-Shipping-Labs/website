---
name: screenshots
description: Capture screenshots of Django pages and attach them to a GitHub issue
disable-model-invocation: true
argument-hint: <issue-number> <url1> [url2...]
---

# Capture Screenshots and Attach to GitHub Issue

Take screenshots of Django pages and upload them to the GitHub issue as inline images.

## Usage

```bash
uv run python scripts/capture_screenshots.py --urls $ARGUMENTS_URLS --issue $ARGUMENTS_ISSUE
```

Parse `$ARGUMENTS` as: `<issue-number> <url1> [url2] [url3] ...`

- First argument is the GitHub issue number
- Remaining arguments are URL paths to screenshot (e.g., `/`, `/pricing/`, `/blog/`)

## Example

For `$ARGUMENTS` = `42 / /pricing/ /blog/`:

```bash
uv run python scripts/capture_screenshots.py --urls / /pricing/ /blog/ --issue 42
```

## What It Does

1. Starts the Django dev server (if not already running)
2. Navigates to each URL with Playwright and captures a full-page screenshot
3. Uploads each PNG to the `screenshots` orphan branch on GitHub at `issue-{N}/{safe_name}.png`
4. Posts a single comment on the issue with all screenshots embedded as `![name](raw_url)` markdown

## How It Works

Screenshots are stored on an orphan branch called `screenshots` in the same repo (`AI-Shipping-Labs/website`). This branch has no shared history with `main` — it exists purely for image hosting.

### Folder structure on the `screenshots` branch

```
screenshots branch
├── .gitkeep
├── issue-70/
│   ├── account.png
│   ├── account_cancel-subscription.png
│   └── pricing.png
├── issue-72/
│   └── blog.png
├── issue-76/
│   └── resources.png
└── issue-{N}/
    └── {page_name}.png
```

Each issue gets its own folder (`issue-{N}/`). Screenshot filenames are derived from the URL path — e.g. `/account/cancel-subscription/` becomes `account_cancel-subscription.png`.

Images are served via `raw.githubusercontent.com`:
```
https://raw.githubusercontent.com/AI-Shipping-Labs/website/screenshots/issue-{N}/{filename}.png
```

### Upload mechanism

The script uses the GitHub Contents API (`gh api --method PUT repos/REPO/contents/PATH --input -`) with the base64-encoded PNG as a JSON payload piped via stdin (to avoid shell argument length limits on large files). If a file already exists at the same path, it fetches the SHA and updates it in place.

## Optional Flags

- `--output DIR` -- save screenshots to a specific local directory (default: temp dir)
- `--repo OWNER/REPO` -- target a different repo (default: `AI-Shipping-Labs/website`)
- `--login-email EMAIL` -- log in as a user before capturing (for authenticated pages)
- `--login-password PASS` -- password for login (default: `testpass123`)
