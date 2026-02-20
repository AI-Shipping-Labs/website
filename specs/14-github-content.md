# 14 - GitHub-Hosted Content

## Overview

All content (articles, courses, resources, projects) is authored and stored in GitHub repositories. The platform pulls content from these repos and renders it on the website. Authors edit content via Git — push to a branch, merge a PR, content appears on the site.

## Architecture

```
GitHub Repos (source of truth)          Platform (Django)
┌─────────────────────────┐            ┌──────────────────────┐
│ ai-shipping-labs/blog   │──webhook──▶│ POST /api/webhooks/  │
│   articles/*.md         │            │      github          │
│   images/               │            │                      │
├─────────────────────────┤            │  1. Pull repo        │
│ ai-shipping-labs/courses│──webhook──▶│  2. Parse markdown   │
│   python-data-ai/       │            │  3. Upsert into DB   │
│     course.yaml         │            │  4. Serve on website  │
│     module-01/          │            │                      │
│       unit-01.md        │            └──────────────────────┘
│       unit-02.md        │
├─────────────────────────┤
│ ai-shipping-labs/       │
│   resources             │──webhook──▶  (same flow)
│   projects/*.md         │
└─────────────────────────┘
```

## GitHub Repos

Each content type lives in its own repo under the `AI-Shipping-Labs` org. Repos can be **public** (for open/free content, community contributions via PRs) or **private** (for paid content like premium articles or courses).

| Repo | Visibility | Content |
|---|---|---|
| `AI-Shipping-Labs/blog` | Public | Blog articles (markdown + images) |
| `AI-Shipping-Labs/courses` | Private | All courses (modules, units, homework) |
| `AI-Shipping-Labs/resources` | Public | Event recordings metadata, curated links, downloadable resource metadata |
| `AI-Shipping-Labs/projects` | Public | Community project showcases |

## Repo Structures

### Blog Repo (`AI-Shipping-Labs/blog`)

```
blog/
├── building-ai-agents-with-mcp.md
├── shipping-features-from-phone.md
├── images/
│   ├── building-ai-agents-with-mcp/
│   │   ├── architecture.png
│   │   └── demo.png
│   └── shipping-features-from-phone/
│       └── pr-screenshot.png
└── README.md
```

Each article is a markdown file with YAML frontmatter:

```yaml
---
title: "Building AI Agents with MCP"
slug: "building-ai-agents-with-mcp"
description: "Learn how to build production-ready AI agents using Model Context Protocol"
date: "2026-02-15"
author: "Alexey Grigorev"
tags: ["ai-engineering", "mcp", "tutorial"]
required_level: 0          # 0=open, 1=basic+, 2=main+, 3=premium
cover_image: "images/building-ai-agents-with-mcp/architecture.png"
---

Article body in markdown...
```

Images are referenced with relative paths: `![diagram](images/building-ai-agents-with-mcp/architecture.png)`. The sync process uploads images to platform storage (S3) and rewrites URLs.

### Courses Repo (`AI-Shipping-Labs/courses`)

```
courses/
├── python-data-ai/
│   ├── course.yaml              # course metadata
│   ├── module-01-setup/
│   │   ├── module.yaml          # module metadata
│   │   ├── unit-01-intro.md     # unit content
│   │   ├── unit-02-environment.md
│   │   └── homework-01.md       # homework (optional)
│   ├── module-02-pandas/
│   │   ├── module.yaml
│   │   ├── unit-01-dataframes.md
│   │   └── unit-02-filtering.md
│   └── images/
│       └── ...
├── ai-hero/
│   ├── course.yaml
│   └── ...
└── README.md
```

`course.yaml`:
```yaml
title: "Python for Data and AI Engineering"
slug: "python-data-ai"
description: "Complete course on Python for data engineering and AI applications"
instructor_name: "Alexey Grigorev"
instructor_bio: "ML engineer and founder of AI Shipping Labs"
cover_image: "images/cover.png"
required_level: 3            # Premium only
is_free: false
discussion_url: "https://ai-shipping-labs.slack.com/archives/C0XXXXX"
tags: ["python", "data-engineering", "ai"]
```

`module.yaml`:
```yaml
title: "Getting Started"
sort_order: 1
```

Unit markdown frontmatter:
```yaml
---
title: "Introduction to the Course"
sort_order: 1
video_url: "https://www.youtube.com/watch?v=XXXXX"
timestamps:
  - time: "0:00"
    label: "Welcome"
  - time: "2:15"
    label: "Course overview"
  - time: "5:30"
    label: "Prerequisites"
is_preview: true             # visible to everyone as a teaser
---

Unit body in markdown...
```

Homework file frontmatter:
```yaml
---
title: "Homework: Set up your environment"
sort_order: 100              # high number to sort after units
is_homework: true
---

Homework instructions...
```

### Resources Repo (`AI-Shipping-Labs/resources`)

```
resources/
├── recordings/
│   ├── building-rag-workshop.yaml
│   └── mcp-deep-dive.yaml
├── curated-links/
│   └── links.yaml           # all curated links in one file
├── downloads/
│   ├── ai-tools-cheatsheet.yaml
│   └── files/
│       └── ai-tools-cheatsheet.pdf
└── README.md
```

Recording YAML:
```yaml
title: "Building RAG Pipelines Workshop"
slug: "building-rag-workshop"
description: "Hands-on workshop recording on building production RAG pipelines"
video_url: "https://www.youtube.com/watch?v=XXXXX"
timestamps:
  - time_seconds: 0
    label: "Introduction"
  - time_seconds: 300
    label: "Architecture overview"
materials:
  - title: "Workshop slides"
    url: "https://docs.google.com/presentation/d/XXXXX"
  - title: "Source code"
    url: "https://github.com/AI-Shipping-Labs/rag-workshop"
tags: ["rag", "workshop"]
required_level: 2
published_at: "2026-02-10"
```

### Projects Repo (`AI-Shipping-Labs/projects`)

```
projects/
├── ai-data-cleaning-assistant.md
├── habit-builder-agent.md
├── images/
│   └── ...
└── README.md
```

Project markdown frontmatter:
```yaml
---
title: "AI Data Cleaning Assistant"
slug: "ai-data-cleaning-assistant"
description: "An AI-powered tool that automatically cleans and normalizes datasets"
author: "Community Member Name"
difficulty: "intermediate"
tags: ["data", "automation", "python"]
source_code_url: "https://github.com/user/ai-data-cleaner"
demo_url: "https://ai-data-cleaner.streamlit.app"
cover_image: "images/ai-data-cleaning-assistant.png"
required_level: 0
---

Project description in markdown...
```

## Sync Process

### Webhook-Based (primary)

1. Each repo has a GitHub webhook configured: on `push` to `main` branch, send POST to `https://aishippinglabs.com/api/webhooks/github`
2. Webhook payload includes repo name and list of changed files
3. Webhook handler:
   a. Validate webhook signature (`X-Hub-Signature-256` header with repo webhook secret)
   b. Identify content type from repo name (`blog` → articles, `courses` → courses, etc.)
   c. Enqueue a background sync job for the repo

### Sync Job

1. Clone or pull the repo to a temp directory. For private repos, use a GitHub App installation token or deploy key.
2. Walk the directory structure, parse all markdown/YAML files
3. For each content item:
   a. Parse frontmatter (YAML) and body (markdown)
   b. Upload images to S3/storage, rewrite image URLs in the body to absolute S3 URLs
   c. Upsert into the database: match by `slug`, create if new, update if changed
   d. Convert markdown body to HTML and store as `content_html`
4. Delete DB records for items whose source files no longer exist in the repo (soft delete or mark as unpublished)
5. Log sync result: `{repo, items_created, items_updated, items_deleted, errors}`

### Manual Sync (fallback)

Admin can trigger a sync from `/admin/sync`:
- "Sync All" button — runs sync for all content repos
- Per-repo "Sync" button — runs sync for a specific repo
- Shows last sync timestamp and result per repo

### GitHub App Authentication

For private repos, the platform uses a GitHub App:
- GitHub App installed on the `AI-Shipping-Labs` org
- App has read-only access to repo contents
- On sync, generate an installation access token via GitHub API
- Use token to clone/pull private repos

## Data Model

```
ContentSource:
  id: uuid
  repo_name: string               # e.g. "AI-Shipping-Labs/blog"
  content_type: string            # "article", "course", "resource", "project"
  webhook_secret: string          # for validating GitHub webhooks
  is_private: bool
  last_synced_at: datetime | null
  last_sync_status: string | null  # "success", "error"
  last_sync_log: text | null

SyncLog:
  id: uuid
  source_id: FK -> ContentSource
  started_at: datetime
  finished_at: datetime | null
  status: string                  # "running", "success", "error"
  items_created: int
  items_updated: int
  items_deleted: int
  errors: jsonb                   # [{file: "path", error: "message"}, ...]
```

Each content table (articles, courses, etc.) gets an additional field:

```
  source_repo: string | null      # e.g. "AI-Shipping-Labs/blog"
  source_path: string | null      # e.g. "building-ai-agents-with-mcp.md"
  source_commit: string | null    # git commit SHA of last sync
```

## Image Handling

1. During sync, find all image references in markdown: `![alt](relative/path.png)`
2. Read the image file from the cloned repo
3. Upload to S3 (or platform storage) under `/content-images/{repo}/{path}`
4. Rewrite the markdown: `![alt](https://cdn.aishippinglabs.com/content-images/blog/images/architecture.png)`
5. Store the rewritten markdown in the database

## Community Contributions

For public repos (blog, projects):
1. Community members fork the repo and submit a PR
2. Repo maintainers review and merge
3. On merge to `main`, webhook fires, sync picks up the new content
4. New content appears on the website with `status = "published"`

For private repos (courses):
- Only org members with write access can contribute
- Same PR workflow, just within the org

## Requirements

- R-GIT-1: Create `content_sources` and `sync_logs` tables with schemas above. Seed with the four repos on first deploy.
- R-GIT-2: Implement `POST /api/webhooks/github` that validates the webhook signature, identifies the repo, and enqueues a sync job.
- R-GIT-3: Implement the sync job: clone/pull repo, parse markdown/YAML, upload images to S3, upsert content into DB, log results.
- R-GIT-4: For private repos, authenticate using a GitHub App installation token. Store GitHub App credentials (`app_id`, `private_key`) in environment config.
- R-GIT-5: Add `source_repo`, `source_path`, `source_commit` fields to all content tables (articles, courses/modules/units, recordings, projects, curated_links, downloads).
- R-GIT-6: During sync, rewrite relative image paths to absolute S3 URLs. Upload images only if they changed (compare file hash).
- R-GIT-7: Admin page `/admin/sync` shows all content sources with last sync time, status, and "Sync Now" button. Shows sync history with item counts and errors.
- R-GIT-8: On sync, soft-delete content items whose source files no longer exist in the repo (set `status = "unpublished"`, don't hard delete).
- R-GIT-9: Admin can still create/edit content directly in the Django admin (for quick fixes). Direct edits are flagged with `source_repo = null`. Next sync from GitHub will overwrite if the same slug exists in the repo.
