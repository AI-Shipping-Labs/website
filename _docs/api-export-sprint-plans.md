# Exporting Sprint Plans via the Production API

## Overview

The plans API lets you pull all member plans from any sprint using token authentication. This doc covers the end-to-end process for exporting, grouping, and saving sprint plan data to a private Gist.

## Prerequisites

- The production API token from `.env` (`API_SHIPPING_LABS_API_TOKEN`)
- `gh` CLI installed and authenticated
- `curl` and `python3` available

## Authentication

The API uses `Authorization: Token <key>` headers (not Bearer). The token must belong to a staff user.

```bash
export API_TOKEN="<production-api-token>"
```

## Step 1: Find the sprint

List all sprints and identify the one you need by `start_date`:

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/sprints | python3 -m json.tool
```

Note: the API redirects trailing slashes (`/api/sprints/` -> `/api/sprints`). Use the slashless form or pass `-L` to follow redirects.

## Step 2: List all plans in the sprint

Use the sprint slug from step 1:

```bash
curl -sL -H "Authorization: Token $API_TOKEN" \
  https://aishippinglabs.com/api/sprints/<slug>/plans | python3 -m json.tool
```

This returns a flat list with `id` and `user_email` for each plan.

## Step 3: Fetch full plan details

Each plan's nested detail (goal, summary, focus, weeks, checkpoints, deliverables, etc.) is at:

```
GET /api/plans/<id>
```

Fetch them all with a loop:

```python
import json, subprocess

TOKEN = "your-token-here"
BASE = "https://aishippinglabs.com/api"

plan_ids = [81, 80, 79, ...]  # from step 2

all_plans = []
for pid in plan_ids:
    result = subprocess.run(
        ["curl", "-sL", "-H", f"Authorization: Token {TOKEN}", f"{BASE}/plans/{pid}"],
        capture_output=True, text=True
    )
    plan = json.loads(result.stdout)
    all_plans.append(plan)

with open("all_plans.json", "w") as f:
    json.dump(all_plans, f, indent=2)
```

## Step 4: Group by profile

Use the plan fields to classify members into groups:

- `summary.current_situation` - where they are now
- `summary.goal` - what they want to achieve
- `summary.main_gap` - what is holding them back
- `focus.main` - primary focus area
- `focus.supporting` - secondary focus areas
- `goal` - 280-char headline

Keyword matching on these fields works well for grouping into categories like:

| Group | Key signals |
|-------|-------------|
| AI Product / Startup Builders | "startup", "saas", "ai app", "ship ai", "product" |
| GenAI & LLM Practitioners | "llm", "rag", "genai", "prompt", "langchain" |
| Transitioning to ML Engineer | "mle", "become ml", "ml engineer" |
| Data Engineers | "data engineer", "pipeline", "etl", "dbt" |
| MLOps & ML Production | "mlops", "deploy", "ml production" |
| Data Science & Modeling | "data scienc", "statistic", "deep learning" |
| Analytics & BI | "analytics", "analyst", "bi", "dashboard" |
| Career Growth & Job Seekers | "interview", "job search", "career" |

## Step 5: Save to a private Gist

```bash
gh gist create --desc "Private: <Sprint Name> plans grouped by profile" grouped_plans.md
```

Gists are secret by default (not public). Do not use `-p` (that makes them public).

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sprints` | GET | List all sprints (optional `?status=active` filter) |
| `/api/sprints/<slug>` | GET | Sprint detail |
| `/api/sprints/<slug>/plans` | GET | List plans in a sprint (flat) |
| `/api/plans/<id>` | GET | Full nested plan detail |

All endpoints require `Authorization: Token <key>` header.

## Privacy

Sprint plans contain personal career information. When exporting:

- Save to private/secret Gists only
- Do not commit plan data to any repository
- Do not share the API token
