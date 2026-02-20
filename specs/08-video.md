# 08 - Video

## Overview

Reusable video player component with clickable timestamps. Used in course units, event recordings, and articles.

## Supported Sources

| Source | Input | Embed method |
|---|---|---|
| YouTube | URL like `https://youtube.com/watch?v=XXX` or `https://youtu.be/XXX` | `<iframe>` with YouTube embed API. Extract video ID from URL. |
| Loom | URL like `https://www.loom.com/share/XXX` | `<iframe>` with Loom embed URL `https://www.loom.com/embed/XXX` |
| Self-hosted | Direct URL to mp4/webm file (S3 or other storage) | HTML5 `<video>` element with `<source>` |

## Timestamp Data

Stored as JSON array on the parent record (unit, recording, or inline in article):

```json
[
  {"time_seconds": 0, "label": "Introduction"},
  {"time_seconds": 125, "label": "Setting up the project"},
  {"time_seconds": 340, "label": "Implementing the API"},
  {"time_seconds": 780, "label": "Testing and deployment"}
]
```

## Video Player Component

A single reusable frontend component `<VideoPlayer>` with props:

```
VideoPlayer:
  video_url: string              # YouTube/Loom/direct URL
  timestamps: array | null       # list of {time_seconds, label}
```

### Layout

- Video embed takes full width of content area
- If timestamps are provided, render a list below the video:
  ```
  [00:00] Introduction
  [02:05] Setting up the project
  [05:40] Implementing the API
  [13:00] Testing and deployment
  ```
- Each timestamp is clickable. Format: `[MM:SS]` for < 1 hour, `[H:MM:SS]` for >= 1 hour

### Click Behavior

- YouTube: use YouTube IFrame API `player.seekTo(time_seconds)`. Initialize player with `enablejsapi=1` parameter.
- Loom: Loom does not support seek via API. Clicking a timestamp appends `?t={time_seconds}` to the embed URL and reloads the iframe.
- Self-hosted: use `videoElement.currentTime = time_seconds`

## Markdown Embed

In article and course unit markdown, a video is embedded by placing a URL on its own line:

```markdown
Some text above.

https://www.youtube.com/watch?v=dQw4w9WgXcQ

Some text below.
```

The markdown renderer detects YouTube/Loom URLs on their own line and replaces them with a `<VideoPlayer>` component (without timestamps — timestamps are only available when explicitly provided via the data model, not inline markdown).

## Requirements

- R-VID-1: Implement a `<VideoPlayer>` frontend component that accepts `video_url` and optional `timestamps`. Detect source type from URL pattern and render the appropriate embed.
- R-VID-2: For YouTube embeds, initialize with YouTube IFrame API. On timestamp click, call `player.seekTo(time_seconds, true)`.
- R-VID-3: For self-hosted video, use HTML5 `<video>` with controls. On timestamp click, set `videoElement.currentTime = time_seconds`.
- R-VID-4: For Loom, embed via iframe. On timestamp click, reload iframe with `?t=` parameter.
- R-VID-5: Format timestamps as `[MM:SS]` (or `[H:MM:SS]` if >= 3600 seconds). Render as a clickable list below the video.
- R-VID-6: In the markdown renderer, detect standalone YouTube and Loom URLs (line contains only a URL matching known patterns) and replace with `<VideoPlayer video_url="...">` (no timestamps).
- R-VID-7: Admin UI for timestamps: when editing a recording or course unit, provide a list editor where each row has a "Time" input (MM:SS format, converted to seconds on save) and a "Label" text input. Rows can be added, removed, and reordered.
