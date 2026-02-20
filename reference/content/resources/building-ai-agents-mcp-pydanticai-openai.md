---
title: "Building AI Agents with MCP, PydanticAI and OpenAI"
description: "Build an AI agent from first principles, implement RAG with FAQ data, compare agent frameworks, and expose tools via MCP for reuse across agents and IDEs."
date: "2025-09-01"
tags: ["ai-agents", "llm-engineering", "agent-systems", "tooling-architecture", "mcp", "rag"]
level: "Intermediate to Advanced"
youtubeUrl: "https://www.youtube.com/watch?v=W2EDdZplLcU"
timestamps:
  - time: "00:00"
    title: "Welcome, Agenda, and Goals"
    description: "Introduction to the workshop, agenda overview, and learning objectives"
  - time: "02:14"
    title: "Setup and Prerequisites (Python, API Keys, uv)"
    description: "Setting up the development environment: Python, API keys configuration, and uv package manager"
  - time: "05:54"
    title: "Agents 101 and Tool Demo; Plan for the FAQ Assistant"
    description: "Understanding agents basics, tool demonstration, and planning the FAQ assistant project"
  - time: "07:50"
    title: "Parse Google Doc to JSON and Index with MinSearch"
    description: "Extracting data from Google Docs, converting to JSON, and indexing with MinSearch for search capabilities"
  - time: "14:30"
    title: "Function Calling with OpenAI Responses API and the Agent Loop"
    description: "Implementing function calling using OpenAI Responses API and building the agent control loop"
  - time: "22:00"
    title: "System/Developer Prompts and Multi-Search Reasoning"
    description: "Crafting effective system and developer prompts, implementing multi-search reasoning patterns"
  - time: "37:00"
    title: "Testing Workflow with a Simple Runner and Chat UI"
    description: "Building a testing workflow with a simple runner and interactive chat interface"
  - time: "53:00"
    title: "Autogenerating Tool Schemas; Adding `add_entry` to Write Back"
    description: "Automatically generating tool schemas and adding write capabilities with add_entry tool"
  - time: "58:00"
    title: "Refactor Tools into a Class; Cleaner Design"
    description: "Refactoring tools into a class-based structure for better organization and maintainability"
  - time: "1:02:00"
    title: "OpenAI Agents SDK and PydanticAI; Swap to Anthropic"
    description: "Comparing OpenAI Agents SDK and PydanticAI frameworks, switching to Anthropic Claude"
  - time: "1:17:22"
    title: "MCP Intro and Building an MCP Server for `search`/`add_entry`"
    description: "Introduction to Model Context Protocol (MCP) and building an MCP server with search and add_entry tools"
  - time: "1:28:32"
    title: "MCP Handshake, Listing Tools, and Calling Tools"
    description: "Understanding MCP protocol: handshake process, listing available tools, and calling tools"
  - time: "1:36:35"
    title: "Using MCP Tools from the Agent; HTTP Transport Option"
    description: "Integrating MCP tools into the agent and exploring HTTP transport for MCP communication"
  - time: "1:41:54"
    title: "Connecting MCP in Cursor and Coding with Live FAQ Context"
    description: "Connecting MCP server to Cursor IDE and using live FAQ context for coding assistance"
  - time: "1:50:30"
    title: "Course Overview and Wrap-up"
    description: "Overview of the AI Engineering Buildcamp course and workshop summary"
materials:
  - title: "Prerequisite: Building a Coding Agent (Python/Django)"
    url: "/event-recordings/building-coding-agent-python-django"
    type: "article"
  - title: "Related Course: AI Engineering Buildcamp"
    url: "https://maven.com/alexey-grigorev/from-rag-to-agents"
    type: "article"
coreTools:
  - "OpenAI API (Responses API, function calling, Agents SDK)"
  - "PydanticAI"
  - "Anthropic API"
  - "MinSearch"
  - "Jupyter Notebook"
  - "uv"
  - "GitHub"
  - "FastMCP"
  - "MCP (Model Context Protocol)"
  - "Cursor IDE"
learningObjectives:
  - "Building an agent from first principles with raw OpenAI APIs"
  - "Implementing function calling and agent loops"
  - "Indexing and querying FAQ data for RAG"
  - "Designing tools with schemas and docstrings"
  - "Refactoring tools into classes"
  - "Comparing agent frameworks (OpenAI Agents SDK vs PydanticAI)"
  - "Switching between LLM providers"
  - "Exposing tools via MCP for reuse across agents and IDEs"
outcome: "A working FAQ assistant agent that can search and update a knowledge base, run across multiple agent frameworks, and consume tools via an MCP server usable by other agents and development environments like Cursor"
relatedCourse: "AI Engineering Buildcamp: From RAG to Agents"
---

This workshop builds on the [Building a Coding Agent: Python/Django Edition](/event-recordings/building-coding-agent-python-django) workshop. Previously, we created a coding agent that could create a Django application from a single prompt. In this workshop, we deep dive into agents.

We build an AI agent step by step, starting from a minimal chatbot and evolving it into a system that can use tools, maintain state through chat history, and run an explicit control loop for multi-step actions. Using a course FAQ dataset, we add retrieval and write capabilities as tools, show how function calling works in real code, and explain why agent "memory" is just structured messages plus control logic. The same agent is then reimplemented with higher-level frameworks like the OpenAI Agents SDK and PydanticAI to clarify what abstractions they provide, before concluding with MCP (Model Context Protocol), where the tools are packaged behind an MCP server so they can be reused by multiple agents and clients, including IDEs, through a standard interface.

## Workshop Overview

Learn how to build AI agents from the ground up, understand different agent frameworks, and create reusable tools via MCP that can be consumed by multiple agents and development environments.

## What You'll Learn

- **First Principles**: Building agents from scratch with raw APIs
- **Function Calling**: Implementing tool use in agents
- **Agent Loops**: Creating multi-step control flows
- **RAG Implementation**: Adding retrieval-augmented generation
- **Tool Design**: Creating well-structured, reusable tools
- **Framework Comparison**: Understanding different agent frameworks
- **MCP Integration**: Exposing tools via Model Context Protocol

## Core Tools and Technologies

- OpenAI API (Responses API, function calling, Agents SDK)
- PydanticAI for agent framework
- Anthropic API for alternative LLM provider
- MinSearch for search and retrieval
- FastMCP and MCP (Model Context Protocol)
- Jupyter Notebook for interactive development
- Cursor IDE for development

## Workshop Structure

### Part 1: Foundation
Start with a minimal chatbot and understand basic LLM interactions.

### Part 2: Function Calling
Add function calling capabilities to enable tool use.

### Part 3: Agent Loops
Implement explicit control loops for multi-step actions.

### Part 4: RAG Implementation
Index FAQ data and add retrieval capabilities as tools.

### Part 5: Tool Design
Design tools with proper schemas, docstrings, and structure.

### Part 6: Tool Refactoring
Refactor tools into classes for better organization.

### Part 7: Framework Comparison
Compare OpenAI Agents SDK vs PydanticAI to understand abstractions.

### Part 8: Provider Switching
Learn how to switch between different LLM providers.

### Part 9: MCP Integration
Package tools behind an MCP server for reuse across agents and IDEs.

## Key Concepts

- **Agent Memory**: Understanding that memory is structured messages plus control logic
- **Tool Abstraction**: How frameworks abstract tool use
- **MCP Standard**: Using Model Context Protocol for tool interoperability
- **State Management**: Maintaining state through chat history

## Expected Outcome

By the end of this workshop, you'll have:
- A working FAQ assistant agent
- Understanding of agent frameworks and their abstractions
- Tools exposed via MCP server
- Ability to use tools in multiple agents and IDEs (like Cursor)
- Knowledge base search and update capabilities

## Prerequisites

- Intermediate to advanced Python skills
- Understanding of LLM APIs
- Familiarity with function calling concepts
- Basic knowledge of agent systems

## Related Course

This workshop is part of the **AI Engineering Buildcamp: From RAG to Agents** course, covering the complete journey from basic chatbots to production agent systems.
