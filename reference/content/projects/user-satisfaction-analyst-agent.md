---
title: "User Satisfaction Analyst Agent"
description: "Carlos Pumar-Frohberg's agent analyzes client satisfaction using Stack Exchange data, focusing on UI discussions to find frustration patterns. An orchestrator agent routes questions to a MongoDB agent for 'what'/'how' queries or a Cipher agent that translates natural language into Neo4j graph queries, often calling both for safety."
date: "2025-12-15"
author: "Carlos Pumar-Frohberg"
tags: ["agents", "Stack Exchange", "MongoDB", "Neo4j", "orchestrator", "graph"]
difficulty: "advanced"
---

![Architecture and processes for Carlos's User Satisfaction Analyst Agent](/images/projects/user-satisfaction-analyst-architecture.png)

Project from the first cohort of the [AI Engineering Buildcamp](https://maven.com/alexey-grigorev/from-rag-to-agents), by **Carlos Pumar-Frohberg**.

The architecture uses a Docker pipeline to fetch Stack Exchange data and dump it into two stores: **MongoDB** for unstructured data and **Neo4j** for graph data. An **orchestrator** agent decides where to route user questions: if the question is about "what" or "how," it goes to the MongoDB agent; if it's about relationships, it goes to a "Cipher" agent that translates natural language into graph queries. Carlos noted that the orchestrator often calls both agents simultaneously to be on the safe side.

**Tech stack:** Docker, MongoDB, Neo4j, orchestrator agent, Cipher (NL → Cypher).

[Carlos's LinkedIn](https://www.linkedin.com/in/carlos-pumar-frohberg/) · [Watch the Demo Day](https://www.youtube.com/watch?v=7RlT8EJH0do)
