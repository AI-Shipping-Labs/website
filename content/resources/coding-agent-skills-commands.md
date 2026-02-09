---
title: "Skills.md from Scratch: Build a Skill-Driven Coding Agent"
description: "Extend a basic coding agent into a general-purpose agent with modular skills and explicit commands. Learn how Claude Code-style skills and commands work internally."
date: "2026-01-16"
tags: ["ai-agents", "llm-engineering", "agent-systems", "tooling-architecture"]
level: "Intermediate to Advanced"
youtubeUrl: "https://youtu.be/OhgDEZfHsvg?si=aGkLTyzaPnmCRFqD"
timestamps:
  - time: "00:00"
    title: "Introduction and Workshop Objectives"
    description: "Welcome and overview of implementing skills and commands. Clarifying what we'll build today and sharing workshop materials."
  - time: "02:00"
    title: "Skills Demo in Claude Code"
    description: "Live demonstration of skills in Claude Code. Showing how to fetch files from GitHub using a skill."
  - time: "08:00"
    title: "Defining Skills vs Commands"
    description: "Understanding the difference: skills are implicit (agent decides), commands are explicit (user specifies). Demo of /kit and /parent commands."
  - time: "10:00"
    title: "Quick Recap: Previous Coding Agent Workshop"
    description: "Review of the coding agent we built previously. Showing the Django website implementation example."
  - time: "13:00"
    title: "Python Environment Setup"
    description: "Setting up the development environment with uv, installing dependencies (Jupyter, OpenAI, ToyAIKit, Python-frontmatter), and configuring OpenAI API key."
  - time: "17:00"
    title: "Core AI Agent Components and Architecture"
    description: "Recap of agent architecture: LLM, instructions, tools, memory. Understanding the agentic loop and tool call patterns."
  - time: "23:00"
    title: "Integrating File System and Bash Tools"
    description: "Setting up file system tools (read_file, write_file, file_tree) and bash command execution. Creating the tools object."
  - time: "31:00"
    title: "Building the General Purpose Coding Runner"
    description: "Creating a general-purpose coding agent using ToyAIKit. Testing with a simple Python script example. Understanding the runner interface."
  - time: "34:00"
    title: "Implementing the Skill Loader Class"
    description: "Creating SkillLoader class to parse markdown frontmatter. Loading skills from directory structure. Understanding skill metadata (name, description, content)."
  - time: "43:00"
    title: "Lazy Loading Strategy for Token Optimization"
    description: "Why we only include skill names/descriptions in prompts, not full content. Implementing lazy loading via tool calls to save tokens."
  - time: "48:00"
    title: "Injecting Dynamic Skills into System Prompts"
    description: "Modifying system prompts to include available skills list. Creating skill injection mechanism. Testing the hello skill."
  - time: "52:00"
    title: "Q&A Session on Model Knowledge and Skills"
    description: "Answering questions about when to use skills vs tools, model prerequisites, limitations, and best practices for creating skills."
  - time: "59:00"
    title: "Developing Slash Commands via Tool Calling"
    description: "Implementing commands loader similar to skills. Creating execute_command tool. Handling slash command parsing and execution."
  - time: "1:11:00"
    title: "Testing Commands Implementation"
    description: "Testing the /kit command execution. Demonstrating how explicit commands work compared to implicit skills."
  - time: "1:19:00"
    title: "Optimizing Prompts for Skill and Command Distinction"
    description: "Adding explicit instructions to help models distinguish between skills and commands. Final testing of the complete system."
  - time: "1:22:00"
    title: "Final Summary and AI Engineering Course Info"
    description: "Workshop wrap-up, key takeaways, and information about the AI Engineering Buildcamp course for AI engineers."
materials:
  - title: "Workshop Code Repository"
    url: "https://github.com/alexeygrigorev/workshops/tree/main/agent-skills"
    type: "code"
  - title: "Prerequisite: Building a Coding Agent (Python/Django)"
    url: "/resources/building-coding-agent-python-django"
    type: "article"
  - title: "Prerequisite: Create a Coding Agent Workshop (GitHub)"
    url: "https://github.com/alexeygrigorev/workshops/tree/main/coding-agent"
    type: "code"
  - title: "ToyAIKit Library"
    url: "https://github.com/alexeygrigorev/toyaikit"
    type: "code"
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
  - "Extending a basic coding agent into a general-purpose coding agent"
  - "Implementing skills as modular autonomously loaded capabilities"
  - "Implementing commands as explicit user-facing shortcuts"
  - "Understanding how Claude Code-style skills and commands work internally"
  - "Reimplementing OpenCode patterns in Python"
  - "Designing agent instructions for general-purpose coding tasks"
  - "Dynamically loading skills via tool calls"
  - "Parsing and executing slash commands without exposing syntax to the agent"
outcome: "A general-purpose coding agent that supports skills and commands, loads capabilities dynamically based on intent, executes explicit slash commands, follows production-style agent control patterns, and mirrors Claude Code-style behavior using a transparent Python implementation"
relatedCourse: "AI Engineering Buildcamp: From RAG to Agents"
---

This workshop builds on the [Building a Coding Agent: Python/Django Edition](/resources/building-coding-agent-python-django) workshop and the [Create a Coding Agent workshop](https://github.com/alexeygrigorev/workshops/tree/main/coding-agent) (GitHub). It extends the agent with skills and commands. It shows how modular capabilities are discovered and loaded automatically, demonstrates how user-facing commands are translated into agent instructions, analyzes OpenCode as a real-world reference, and incrementally builds a skill-driven coding agent that mirrors modern AI coding tools while remaining fully inspectable and framework-agnostic.

## Workshop Overview

Learn how to transform a basic coding agent into a sophisticated, general-purpose agent system that can dynamically load capabilities and execute explicit commands—similar to how Claude Code and other modern AI coding assistants work.

## What You'll Learn

- **Agent Architecture**: How to structure a modular agent system
- **Skills System**: Implementing autonomously loaded capabilities
- **Command System**: Creating explicit user-facing shortcuts
- **OpenCode Patterns**: Understanding and reimplementing production patterns
- **Dynamic Loading**: Loading capabilities based on intent
- **Framework Design**: Building inspectable, framework-agnostic systems

## Core Tools and Technologies

- OpenAI API (Responses API, function calling, Agents SDK)
- PydanticAI for agent framework
- Anthropic API for alternative LLM provider
- MinSearch for search capabilities
- Jupyter Notebook for interactive development
- uv for Python package management
- FastMCP and MCP (Model Context Protocol)
- Cursor IDE for development

## Workshop Structure

### Part 1: Foundation
Start with an existing coding agent and understand its current capabilities.

### Part 2: Skills Implementation
Build a modular skills system that can be discovered and loaded automatically based on user intent.

### Part 3: Commands System
Implement explicit slash commands that translate user requests into agent instructions.

### Part 4: OpenCode Analysis
Study OpenCode as a real-world reference and understand production patterns.

### Part 5: Integration
Combine skills and commands into a unified agent system that mirrors modern AI coding tools.

## Expected Outcome

By the end of this workshop, you'll have built:
- A general-purpose coding agent with skills and commands
- Dynamic capability loading based on intent
- Explicit slash command execution
- Production-style agent control patterns
- A transparent Python implementation that mirrors Claude Code behavior

## Prerequisites

- Intermediate to advanced Python skills
- Understanding of LLM APIs and function calling
- Familiarity with agent systems
- Basic knowledge of tooling architecture

## Related Course

This workshop complements the **AI Engineering Buildcamp: From RAG to Agents** course, which covers the full spectrum of building production AI agent systems.
