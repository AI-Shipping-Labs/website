# 092 - GitHub Content Sync

**Status:** pending
**Tags:** `content`, `integration`, `admin`, `infra`
**GitHub Issue:** [#92](https://github.com/AI-Shipping-Labs/website/issues/92)
**Specs:** 14
**Depends on:** [093-background-jobs](093-background-jobs.md), [072-articles](072-articles.md), [074-recordings](074-recordings.md), [075-projects](075-projects.md), [076-curated-links](076-curated-links.md), [077-downloads](077-downloads.md), [078-course-models-catalog](078-course-models-catalog.md)
**Blocks:** —

## Scope

- ContentSource model: repo_name, content_type, webhook_secret, is_private, last_synced_at, last_sync_status, last_sync_log
- SyncLog model: source FK, started_at, finished_at, status, items_created/updated/deleted, errors (JSON)
- Add source_repo, source_path, source_commit fields to all content tables
- Seed four content sources: blog, courses, resources, projects repos
- Webhook `POST /api/webhooks/github`: validate signature, identify repo, enqueue sync job
- Sync job: clone/pull repo, parse markdown/YAML frontmatter, upload images to S3 (skip unchanged by hash), rewrite image URLs, upsert content into DB by slug
- Soft-delete content whose source files no longer exist in repo
- GitHub App authentication for private repos (app_id + private_key in config)
- Admin `/admin/sync` page: content sources list, last sync time/status, "Sync Now" button per repo and "Sync All", sync history with item counts and errors
- Direct admin edits flagged with source_repo = null; next sync overwrites if slug exists in repo

## Acceptance Criteria

- [ ] ContentSource model with fields: repo_name, content_type, webhook_secret, is_private (bool), last_synced_at, last_sync_status, last_sync_log (text)
- [ ] SyncLog model: source FK, started_at, finished_at, status (success/partial/failed), items_created, items_updated, items_deleted, errors (JSON)
- [ ] All content tables get additional fields: source_repo, source_path, source_commit
- [ ] Four default content sources seeded: blog, courses, resources, projects repos
- [ ] `POST /api/webhooks/github` validates X-Hub-Signature-256 header; identifies repo from payload; enqueues sync job
- [ ] Sync job: clones/pulls repo, parses markdown + YAML frontmatter, uploads images to storage (skips unchanged by content hash), rewrites image URLs in content, upserts content into DB by slug
- [ ] Content whose source files no longer exist in repo is soft-deleted (not hard-deleted)
- [ ] GitHub App authentication: app_id + private_key in config; generates installation tokens for private repo access
- [ ] Admin `/admin/sync` page: content sources list with last sync time/status, "Sync Now" button per repo, "Sync All" button
- [ ] Admin sync history: shows SyncLog entries with item counts and error details
- [ ] Direct admin edits flagged with source_repo = null; next sync from repo overwrites by slug match
