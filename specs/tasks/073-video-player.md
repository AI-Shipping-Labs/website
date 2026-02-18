# 073 - Video Player Component

**Status:** deferred (needs grooming)
**Tags:** `frontend`
**GitHub Issue:** [#73](https://github.com/AI-Shipping-Labs/website/issues/73)
**Specs:** 08
**Depends on:** [001-scaffold](001-scaffold.md)
**Blocks:** [074-recordings](074-recordings.md), [078-course-models-catalog](078-course-models-catalog.md)

## Scope

- Reusable VideoPlayer component accepting video_url and optional timestamps
- Detect source from URL: YouTube, Loom, self-hosted (mp4/webm)
- YouTube: iframe embed with IFrame API, seekTo on timestamp click
- Loom: iframe embed, reload with ?t= on timestamp click
- Self-hosted: HTML5 video element, currentTime on timestamp click
- Timestamp list below video: clickable [MM:SS] or [H:MM:SS] labels
- Markdown renderer integration: detect standalone YouTube/Loom URLs and replace with VideoPlayer (no timestamps for inline)
- Admin timestamp editor: add/remove/reorder rows with time (MM:SS) + label inputs

## Acceptance Criteria

- VideoPlayer renders correctly for YouTube, Loom, and self-hosted videos
- Timestamps display formatted correctly and seeking works for each source type
- Standalone video URLs in markdown auto-embed as VideoPlayer
- Admin UI provides timestamp list editor
- R-VID-1 through R-VID-7 satisfied
