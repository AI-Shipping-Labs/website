---
title: "Building Safe AI Agents with Guardrails"
description: "Build safe AI agents with input and output guardrails. Learn how to prevent inappropriate responses, enforce policies, and maintain academic integrity."
date: "2026-01-06"
tags: ["ai-agents", "llm-engineering", "agent-safety", "tooling-architecture", "async-control"]
level: "Intermediate to Advanced"
youtubeUrl: "https://www.youtube.com/watch?v=Sk1aqwNJWT4"
timestamps:
  - time: "00:00"
    title: "Workshop Prerequisites and OpenAI Agents SDK"
    description: "Setting up the environment and understanding OpenAI Agents SDK requirements"
  - time: "03:09"
    title: "What are Guardrails and Why We Need Them"
    description: "Introduction to guardrails as safety checks for AI agents. Understanding their purpose and importance."
  - time: "06:14"
    title: "Building the Search Engine for Data Engineering Course FAQ"
    description: "Creating the base FAQ assistant with search capabilities using course data"
  - time: "16:34"
    title: "Creating an Agent with OpenAI Agents SDK"
    description: "Building the agent system using OpenAI Agents SDK with tool integration"
  - time: "18:50"
    title: "Protecting Agents with Input Guardrails"
    description: "Implementing input guardrails to block irrelevant or harmful queries before the agent processes them"
  - time: "29:05"
    title: "Preventing Unwanted Behavior with Output Guardrails"
    description: "Adding output guardrails to validate responses before users see them, preventing policy violations"
  - time: "37:04"
    title: "Analyzing Guardrail Context and Metadata"
    description: "Understanding how guardrails analyze context and metadata to make decisions"
  - time: "39:42"
    title: "Implementing Guardrails with Tool Calls for Unsupported Frameworks"
    description: "Building framework-agnostic guardrail implementation using tool calls for frameworks without native support"
  - time: "47:50"
    title: "Running Guardrails in Parallel Using Asyncio and Tasks"
    description: "Optimizing guardrail execution by running multiple guardrails concurrently with asyncio to avoid added latency"
  - time: "1:09:52"
    title: "Summary of Guardrail Implementation and Upcoming Workshops"
    description: "Recap of key concepts, implementation patterns, and information about future workshops"
materials:
  - title: "Workshop Code Repository"
    url: "https://github.com/alexeygrigorev/workshops/tree/main/guardrails"
    type: "code"
  - title: "Related Course: AI Engineering Buildcamp"
    url: "https://maven.com/alexey-grigorev/from-rag-to-agents"
    type: "article"
coreTools:
  - "OpenAI API"
  - "OpenAI Agents SDK (guardrails, Runner)"
  - "Pydantic (structured outputs)"
  - "MinSearch (FAQ index)"
  - "Jupyter Notebook"
  - "uv"
  - "GitHub"
  - "Python asyncio"
learningObjectives:
  - "Defining guardrails as LLM-based safety checks"
  - "Implementing input guardrails to block irrelevant or harmful queries"
  - "Implementing output guardrails to validate responses"
  - "Preventing inappropriate promises like deadline extensions or legal advice"
  - "Enforcing academic integrity by blocking homework-writing"
  - "Chaining multiple guardrails with early stop behavior"
  - "Running guardrails with streaming safely"
  - "Implementing tool-based guardrails for frameworks without native support"
  - "Using asyncio to run guardrails concurrently"
  - "Cancelling the agent early when a guardrail trips"
  - "Building a framework-agnostic DIY guardrail runner"
outcome: "A DataTalks.Club FAQ assistant protected by input and output guardrails that block off-topic questions, unsafe or policy-violating responses, and academic dishonesty, supports multiple guardrails with clear failure handling, works with streaming, and includes a reusable async pattern to add guardrails to any agent framework"
relatedCourse: "AI Engineering Buildcamp: From RAG to Agents"
---

This workshop builds a working FAQ assistant first, then layers guardrails on top as structured pass/fail checks, covers input and output validation with clear tripwire handling, shows how to chain guardrails for safety and academic integrity, demonstrates streaming behavior where validation happens before output begins, and finishes with a framework-agnostic async pattern that runs guardrails in parallel and cancels the agent immediately when a check fails.

## Workshop Overview

Learn how to build safe, production-ready AI agents by implementing comprehensive guardrail systems that validate both inputs and outputs, enforce policies, and maintain academic integrity.

## What You'll Learn

- **Guardrail Fundamentals**: Understanding LLM-based safety checks
- **Input Validation**: Blocking queries before the agent processes them
- **Output Validation**: Ensuring responses meet safety and policy requirements
- **Policy Enforcement**: Preventing inappropriate promises and advice
- **Academic Integrity**: Blocking homework-writing and academic dishonesty
- **Streaming Safety**: Validating outputs during streaming without exposing unsafe content
- **Framework Integration**: Adding guardrails to any agent framework

## Core Tools and Technologies

- OpenAI API and Agents SDK (guardrails, Runner)
- Pydantic for structured outputs
- MinSearch for FAQ indexing
- Python asyncio for concurrent guardrail execution
- Jupyter Notebook for interactive development
- uv for package management

## Workshop Structure

### Part 1: Base Agent
Build a working FAQ assistant that can answer questions from a knowledge base.

### Part 2: Input Guardrails
Implement guardrails that check queries before the agent runs, blocking off-topic or harmful requests.

### Part 3: Output Guardrails
Add validation that checks agent responses before users see them, ensuring they meet safety and policy requirements.

### Part 4: Policy Enforcement
Implement specific guardrails for:
- Preventing inappropriate promises (deadline extensions, legal advice)
- Enforcing academic integrity
- Blocking policy-violating content

### Part 5: Advanced Patterns
- Chaining multiple guardrails
- Running guardrails concurrently with asyncio
- Early cancellation when guardrails trip
- Streaming-safe validation

### Part 6: Framework-Agnostic Pattern
Build a reusable guardrail runner that works with PydanticAI, LangChain, or custom agents.

## Expected Outcome

By the end of this workshop, you'll have:
- A FAQ assistant protected by comprehensive guardrails
- Input validation that blocks off-topic questions
- Output validation that prevents unsafe responses
- Academic integrity enforcement
- A reusable async guardrail pattern
- Framework-agnostic implementation usable with any agent system

## Key Features

- **Early Stop Behavior**: Guardrails trip before unsafe content is generated
- **Streaming Safety**: Validation happens before output begins
- **Cost Efficiency**: Early cancellation saves tokens and costs
- **Concurrent Execution**: Multiple guardrails run in parallel
- **Framework Agnostic**: Works with any agent framework

## Prerequisites

- Intermediate to advanced Python skills
- Understanding of LLM APIs and agent systems
- Familiarity with async programming (asyncio)
- Basic knowledge of safety and policy requirements

## Related Course

This workshop complements the **AI Engineering Buildcamp: From RAG to Agents** course, covering safety and reliability in production agent systems.
