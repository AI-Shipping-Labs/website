---
title: "To-Do List Agent"
description: "A reference project from the AI Engineering Buildcamp: an agent that interacts with a simple to-do list application. Built with Lovable for the frontend and FastAPI (Python) for the backend. Uses the backend's OpenAPI spec so the model can create tools to get tasks or mark them complete, with Logfire for monitoring and pytest for testing."
date: "2025-12-15"
author: "Alexey Grigorev"
tags: ["agents", "Lovable", "FastAPI", "OpenAPI", "Logfire"]
difficulty: "intermediate"
---

![my-daily-tasks-agent repository on GitHub](/images/projects/to-do-list-agent.png)

Reference project from the first cohort of the [AI Engineering Buildcamp](https://maven.com/alexey-grigorev/from-rag-to-agents), built to help students understand the requirements for a robust, end-to-end AI application.

The agent does not use RAG or a knowledge base. Instead, it relies on the backend's OpenAPI specification: the spec is fed to the model so it can create tools to get tasks or mark them as complete. Monitoring is done with **Logfire**, which tracks the entire session, including the specific tools used and the cost of each interaction. The project includes **18 pytest tests** covering different scenarios, such as checking whether the right tool is invoked when asking about today's tasks.

**Tech stack:** Lovable (frontend), FastAPI (Python backend), OpenAPI, Logfire, pytest.

[View the project on GitHub](https://github.com/alexeygrigorev/my-daily-tasks-agent) · [Watch the Demo Day](https://www.youtube.com/watch?v=7RlT8EJH0do)
