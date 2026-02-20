---
title: "Habit Builder Agent"
description: "Vancesca Dinh's agent helps users identify goals and understand the 'why' behind them, grounded in data from the Huberman Lab podcast and medical publications. Pipeline includes RSS feeds, Faster Whisper transcription, Qdrant embeddings, query rewriting for better search, Logfire, Pydantic, and guardrails to keep the agent on-task."
date: "2025-12-15"
author: "Vancesca Dinh"
tags: ["agents", "RAG", "Qdrant", "Faster Whisper", "Huberman Lab", "guardrails"]
difficulty: "advanced"
---

![Architecture of Vancesca's habit builder agent](/images/projects/habit-builder-agent-architecture.png)

Project from the first cohort of the [AI Engineering Buildcamp](https://maven.com/alexey-grigorev/from-rag-to-agents), by **Vancesca Dinh**.

The Habit Builder agent is grounded in data from the Huberman Lab podcast and medical publications. The data pipeline includes: downloading RSS feeds, transcribing audio with **Faster Whisper**, and storing embeddings in a **Qdrant** vector database. For the agent, she implemented a tool that rewrites user queries in three different ways to improve search results. She used **Logfire** for logging and **Pydantic** for structure. A key part of her presentation was the need for **guardrails**: she found the agent would obediently "draw a cute pig" or translate text into Romanian if asked, so she added checks to keep it focused on the intended use.

**Tech stack:** RSS, Faster Whisper, Qdrant, query rewriting, Logfire, Pydantic, guardrails.

[Vancesca's LinkedIn](https://www.linkedin.com/in/vancesca-dinh/) · [Watch the Demo Day](https://www.youtube.com/watch?v=7RlT8EJH0do)
