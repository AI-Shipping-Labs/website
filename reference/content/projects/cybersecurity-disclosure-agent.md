---
title: "Cybersecurity Disclosure Agent"
description: "An agent that tracks cybersecurity incidents reported to the SEC, built by Scott DeGeest. Helps supply chain professionals quickly find out if a company has disclosed a data breach or ransomware attack. Handles raw XML/PDF from the SEC, invalid XML, subsidiary relationships (e.g. Change Healthcare ↔ United Health Group), and token monitoring for cost and context limits."
date: "2025-12-15"
author: "Scott DeGeest"
tags: ["agents", "SEC", "Elasticsearch", "supply chain", "RAG"]
difficulty: "advanced"
---

![Scott's Cybersecurity Disclosure Agent generating a reply](/images/projects/cybersecurity-disclosure-agent.png)

Project from the first cohort of the [AI Engineering Buildcamp](https://maven.com/alexey-grigorev/from-rag-to-agents), by **Scott DeGeest** (Principal Data Scientist).

The system downloads raw files (often in XML or PDF) from the SEC website. Scott built logic to handle both valid and invalid XML structures. Data is converted and indexed in **Elasticsearch**. A key challenge was modeling subsidiaries so the agent knows that e.g. "Change Healthcare" is related to "United Health Group." He also added a monitor for input and output tokens to keep an eye on costs and context limits.

**Tech stack:** SEC data ingestion, XML/PDF parsing, Elasticsearch, token monitoring.

[Scott's LinkedIn](https://www.linkedin.com/in/dscottdegeest/) · [Watch the Demo Day](https://www.youtube.com/watch?v=7RlT8EJH0do)
