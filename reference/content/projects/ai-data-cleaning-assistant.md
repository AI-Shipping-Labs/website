---
title: "AI Data Cleaning Assistant"
description: "A production-style app that automates messy CSV cleanup: upload a file, profile it, generate AI-backed cleaning steps, apply deterministic transformations, and download a cleaned dataset with a human-readable report. Turns a repetitive, error-prone task into a repeatable pipeline with tests, an API, and Cloud Run deployment. LLM acts as planner, cleaning engine is deterministic for reliability."
date: "2025-12-15"
author: "deedeepratiwi"
tags: ["data cleaning", "FastAPI", "React", "LLM", "MCP", "Cloud Run", "OpenAPI"]
difficulty: "intermediate"
---

An **AI-powered data cleaning assistant** that accepts CSV or Excel via web UI or API, analyzes dataset structure and data quality issues, generates cleaning suggestions using intelligent detection, and applies transformations deterministically. You get a cleaned dataset plus a human-readable report of what changed.

**Why it's useful:** Raw tabular data is often messy: inconsistent column names, missing values, mixed types, duplicates. This project turns that into a **repeatable pipeline**: the LLM acts as a **planner** (suggesting what to do), while the **cleaning engine** is deterministic and testable. The repo includes unit tests, integration tests, E2E tests (Playwright), OpenAPI-first design, Docker, and deployment to **Google Cloud Run**.

**Features:** Column standardization (snake_case), non-value detection (ERROR/UNKNOWN → NaN), string normalization, smart type casting (numeric, datetime), duplicate removal, null handling, ID-column protection. The system uses MCP-style boundaries: structured JSON in/out between the LLM and the execution layer.

**Tech stack:** FastAPI (Python), React + TypeScript, OpenAI-compatible LLM, Pandas, SQLite/Postgres, OpenAPI, Docker, GitHub Actions, GCP Cloud Run.

[View on GitHub](https://github.com/deedeepratiwi/ai-data-cleaning-assistant) · [Live API (GCP Cloud Run)](https://ai-data-cleaning-api-arnhwr7jpa-et.a.run.app)
