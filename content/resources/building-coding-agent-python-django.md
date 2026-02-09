---
title: "Building a Coding Agent: Python/Django Edition"
description: "Build your own project bootstrapper—a coding agent similar to Lovable, but for Python/Django projects. Learn how to create a tool-using agent that can bootstrap complete applications from natural language instructions."
date: "2025-08-14"
tags: ["ai-agents", "coding-agents", "django", "python", "llm-engineering"]
level: "Intermediate to Advanced"
youtubeUrl: "https://www.youtube.com/watch?v=Sue_mn0JCsY"
timestamps:
  - time: "00:00"
    title: "Introduction & Workshop Goals"
    description: "Overview of the workshop objectives and what we'll build"
  - time: "00:09"
    title: "Presenter Background & Experience"
    description: "Introduction to the presenter's background in AI and coding agents"
  - time: "01:08"
    title: "Example: What is 'Lovable'? (The Goal)"
    description: "Understanding Lovable as inspiration for building our own coding agent"
  - time: "02:18"
    title: "The Plan: Building a Python/Django Coding Agent"
    description: "Outlining the approach to build a coding agent for Python/Django projects"
  - time: "03:43"
    title: "Tools Setup: OpenAI API Keys & Budget"
    description: "Setting up OpenAI API keys and configuring budget limits"
  - time: "05:02"
    title: "Environment: Using GitHub Codespaces"
    description: "Setting up the development environment with GitHub Codespaces"
  - time: "11:20"
    title: "Concept: Agents vs. Chatbots (Adding Tools)"
    description: "Understanding the difference between chatbots and agents, and how tools enable agentic behavior"
  - time: "16:48"
    title: "Defining System Prompts & User Prompts"
    description: "Crafting effective system prompts and user prompts for the coding agent"
  - time: "24:00"
    title: "Setting up the Django Project Template"
    description: "Preparing a Django project template as the base for the agent to work with"
  - time: "29:01"
    title: "Defining Agent Tools (Read/Write Files, Search)"
    description: "Implementing core tools: file reading, writing, and search capabilities"
  - time: "33:36"
    title: "Implementing & Testing Tools in Jupyter"
    description: "Building and testing the agent tools interactively in Jupyter notebooks"
  - time: "37:27"
    title: "Crafting the 'Developer Prompt' (Crucial Step)"
    description: "Creating the critical developer prompt that guides the agent's behavior"
  - time: "42:16"
    title: "Running the Agent with ToyAIKit"
    description: "Executing the coding agent using ToyAIKit framework"
  - time: "47:49"
    title: "Recap & Break"
    description: "Reviewing what we've built so far and taking a break"
  - time: "50:42"
    title: "Intro to OpenAI Agents SDK"
    description: "Introduction to OpenAI's Agents SDK for building production agents"
  - time: "53:48"
    title: "Building a Joke Agent with Agents SDK"
    description: "Creating a simple joke agent to understand Agents SDK patterns"
  - time: "58:08"
    title: "Porting the Coding Agent to Agents SDK"
    description: "Migrating our Django coding agent to use OpenAI Agents SDK"
  - time: "1:04:07"
    title: "Intro to PydanticAI (Production Grade Framework)"
    description: "Introduction to PydanticAI as a production-ready framework for agents"
  - time: "1:06:50"
    title: "Creating the Agent with PydanticAI"
    description: "Rebuilding the coding agent using PydanticAI framework"
  - time: "1:10:56"
    title: "Switching to Anthropic Claude 3.5 Sonnet (Better Code)"
    description: "Comparing models and switching to Claude for better code generation"
  - time: "1:15:34"
    title: "Test Case: Building an Anki Cards App"
    description: "Testing the agent by building a complete Anki cards application"
  - time: "1:16:16"
    title: "Testing Z.AI Models (Reasoning Models)"
    description: "Exploring reasoning models from Z.AI for advanced agent capabilities"
  - time: "1:25:45"
    title: "Course Overview & Learning Path"
    description: "Overview of the AI Engineering Buildcamp course and learning journey"
materials:
  - title: "Related: AI Coding Tools Compared Workshop"
    url: "/resources/ai-coding-tools-compared"
    type: "article"
  - title: "Related Course: AI Engineering Buildcamp"
    url: "https://maven.com/alexey-grigorev/from-rag-to-agents"
    type: "article"
coreTools:
  - "OpenAI API"
  - "OpenAI Agents SDK"
  - "PydanticAI"
  - "Anthropic Claude API"
  - "ToyAIKit"
  - "Django"
  - "Python"
  - "Jupyter Notebook"
  - "GitHub Codespaces"
  - "Z.AI Models"
learningObjectives:
  - "Understanding the difference between chatbots and agents"
  - "Building a project bootstrapper from scratch for Python/Django projects"
  - "Implementing agent tools (file operations, search)"
  - "Crafting effective system and developer prompts"
  - "Using ToyAIKit for interactive agent development"
  - "Migrating to OpenAI Agents SDK"
  - "Building production-ready agents with PydanticAI"
  - "Comparing different LLM providers (OpenAI, Anthropic, Z.AI)"
  - "Testing agents with real-world projects"
  - "Understanding how project bootstrappers work internally"
outcome: "A fully functional project bootstrapper—a coding agent that can build Django applications based on user instructions, implemented using multiple frameworks (ToyAIKit, OpenAI Agents SDK, PydanticAI), with the ability to read/write files, search code, and generate complete applications from scratch"
relatedCourse: "AI Engineering Buildcamp: From RAG to Agents"
---

In the [AI Coding Tools Compared](/resources/ai-coding-tools-compared) workshop, we explored different categories of AI-assisted development tools, including "Project Bootstrappers" like Lovable and claude-code CLI. Now, we're building one ourselves.

This workshop guides you through building your own project bootstrapper—a coding agent similar to Lovable, but specifically designed for Python/Django projects. You'll learn how to transform a basic chatbot into a powerful tool-using agent that can read files, write code, search codebases, and build complete Django applications based on natural language instructions.

## Workshop Overview

Learn how to build a project bootstrapper—a coding agent that can understand user requirements and automatically generate Django applications. This workshop covers the complete journey from basic chatbots to production-ready agents using multiple frameworks. You'll create your own version of tools like Lovable, but tailored for Python/Django development.

## What You'll Learn

- **Agents vs Chatbots**: Understanding how tools enable agentic behavior
- **Agent Architecture**: Building agents with system prompts, tools, and control loops
- **Tool Implementation**: Creating file operations and search tools
- **Framework Comparison**: Working with ToyAIKit, OpenAI Agents SDK, and PydanticAI
- **Model Selection**: Comparing OpenAI, Anthropic Claude, and Z.AI models
- **Real-World Testing**: Building complete applications like Anki cards app

## Core Tools and Technologies

- OpenAI API and Agents SDK
- PydanticAI for production agents
- Anthropic Claude 3.5 Sonnet
- ToyAIKit for interactive development
- Django for web applications
- Jupyter Notebook for experimentation
- GitHub Codespaces for development environment
- Z.AI reasoning models

## Workshop Structure

### Part 1: Foundation
- Understanding Lovable as inspiration
- Setting up development environment
- Concepts: agents vs chatbots

### Part 2: Building the Basic Agent
- Setting up Django project template
- Implementing core tools (read/write files, search)
- Crafting developer prompts
- Running with ToyAIKit

### Part 3: Production Frameworks
- Migrating to OpenAI Agents SDK
- Building with PydanticAI
- Comparing different LLM providers

### Part 4: Testing and Evaluation
- Building real applications (Anki cards app)
- Testing with different models
- Exploring reasoning models

## Expected Outcome

By the end of this workshop, you'll have:
- A working coding agent for Django projects
- Understanding of multiple agent frameworks
- Experience with different LLM providers
- Ability to build complete applications from instructions
- Knowledge of production-ready agent patterns

## Key Concepts

- **Tool-Using Agents**: How agents differ from chatbots through tool access
- **Developer Prompts**: Critical instructions that guide agent behavior
- **Framework Comparison**: Understanding trade-offs between different agent frameworks
- **Model Selection**: Choosing the right LLM for coding tasks

## Prerequisites

- Intermediate Python skills
- Basic understanding of Django
- Familiarity with LLM APIs
- Experience with Jupyter notebooks

## Related Course

This workshop is part of the **AI Engineering Buildcamp: From RAG to Agents** course, which covers building production-ready AI agent systems from scratch.
