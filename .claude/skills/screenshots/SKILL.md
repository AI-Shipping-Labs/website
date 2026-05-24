---
name: screenshots
description: Upload a screenshot file and get back a shareable CloudFront URL
disable-model-invocation: true
argument-hint: <file-path> [file-path...]
---

# Share a Screenshot URL With the User

Upload a local PNG (or JPG/WEBP/GIF) to the `sandbox-screenshots` service and surface the returned CloudFront URL back to the user. This is the canonical procedure any agent should follow whenever it needs to share an image — issue comments, design audits, QA reports, ad-hoc replies.

Screenshot capture (Playwright, viewport handling, dev server bootstrap) is unchanged and still lives in `scripts/capture_screenshots.py`. This skill picks up after the PNG exists on disk.

## Precondition: `upload-screenshot` must be reachable

Before uploading, confirm the CLI is reachable:

```bash
command -v upload-screenshot
```

If it resolves, use the bare command in the Upload section below.

### Fallback: CLI installed but not on `$PATH`

`install.sh` adds the venv to `$PATH` via `~/.bashrc`, but non-interactive agent subshells do not source `~/.bashrc`. If `command -v upload-screenshot` returns nothing, check whether the binary exists at the venv path:

```bash
ls ~/git/sandbox-screenshots/.venv/bin/upload-screenshot
```

If that file exists, the install ran — the subshell just cannot see the updated `$PATH`. Use the absolute path for every `upload-screenshot` invocation in this skill (and substitute it everywhere this skill writes `upload-screenshot`):

```bash
~/git/sandbox-screenshots/.venv/bin/upload-screenshot /path/to/screenshot.png
```

### If the CLI is not installed at all

If `~/git/sandbox-screenshots/.venv/bin/upload-screenshot` does not exist, do NOT auto-install. Stop and fail loudly with a one-line instruction for the operator:

```
upload-screenshot is not installed. Run ~/git/sandbox-screenshots/install.sh, then re-run this step.
```

Installation is a one-time human action. Agents must never write to the operator's shell config or run the install script on their behalf.

## Upload

```bash
upload-screenshot /path/to/screenshot.png
```

The CLI prints JSON on stdout:

```json
{"url":"https://d31nukezbn4e3o.cloudfront.net/YYYY/MM/DD/object.png","key":"YYYY/MM/DD/object.png"}
```

Return the `url` value to the user. If uploading multiple files, run the CLI once per file and collect each `url`.

For a URL-only output (no JSON wrapper), use:

```bash
upload-screenshot --url-only /path/to/screenshot.png
```

Supported content types: `image/png`, `image/jpeg`, `image/webp`, `image/gif`. The CLI infers the content type from the file extension. Uploaded objects are deleted automatically by an S3 lifecycle rule after 60 days, so these URLs are suitable for throw-away sharing only.

## Token hygiene (MANDATORY)

The CLI loads `SCREENSHOT_UPLOAD_TOKEN` from `~/git/sandbox-screenshots/.env`. That file is the only place the token should ever exist on disk.

Never paste the literal `SCREENSHOT_UPLOAD_TOKEN` value into:

- this repo (skill docs, agent prompts, scripts, Makefile)
- GitHub issue comments, PR descriptions, or commit messages
- chat output, audit reports, test logs, or screenshots
- the user's shell history (do not echo it, do not export it inline)

You may reference the variable name `SCREENSHOT_UPLOAD_TOKEN` in prose when explaining this rule. You may never reproduce its value.

## End-to-end example

Capture a page, upload the resulting PNG, return the URL:

```bash
uv run python scripts/capture_screenshots.py --urls / --output .tmp/screenshots
upload-screenshot .tmp/screenshots/home.png
```

Then surface the printed `url` back to the user.

## Tester usage

The tester agent captures a batch of pages, uploads each PNG, and posts a single `## Screenshots` comment to the issue. See `.claude/agents/tester.md` Step 7 for the exact comment format.

## Designer usage

The designer agent embeds CloudFront URLs (returned by `upload-screenshot`) in the "Screenshots" sub-section of the audit report. See `.claude/agents/designer.md` for the report template.

## Why this exists

Screenshots used to be pushed to an internal hosting path and shared via long internal URLs. That mechanism is retired. The `sandbox-screenshots` service is the only supported upload path going forward. Agents that share an image any other way will produce broken links.
