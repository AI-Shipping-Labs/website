---
title: "Build a Production-Ready YouTube AI Agent with Temporal"
description: "Build a durable data ingestion pipeline, handle IP blocking with proxies, index transcripts into ElasticSearch, and design a multi-stage research agent with Temporal orchestration."
date: "2025-12-16"
tags: ["ai-agents", "data-engineering", "temporal", "elasticsearch", "production-systems"]
level: "Advanced"
youtubeUrl: "https://www.youtube.com/watch?v=N1gaI3Qz6vw&t=2904s"
timestamps:
  - time: "00:00"
    title: "Introduction & Project Overview"
    description: "Welcome and overview of building a production-ready YouTube AI agent with Temporal orchestration"
  - time: "02:15"
    title: "Environment Setup (GitHub Codespaces vs. Local)"
    description: "Setting up the development environment, comparing GitHub Codespaces and local setup options"
  - time: "07:13"
    title: "Part 1: Data Ingestion Workflow Setup"
    description: "Beginning the data ingestion workflow: planning and initial setup"
  - time: "12:00"
    title: "Formatting Transcripts & Subtitles"
    description: "Processing and formatting YouTube transcripts and subtitles for indexing"
  - time: "16:05"
    title: "Deploying ElasticSearch with Docker"
    description: "Setting up ElasticSearch using Docker for search infrastructure"
  - time: "19:39"
    title: "Configuring ElasticSearch Indices & Stop Words"
    description: "Configuring ElasticSearch indices, mappings, and stop words for optimal search"
  - time: "30:52"
    title: "Iterating Through Videos & Progress Tracking"
    description: "Building the video iteration logic and implementing progress tracking"
  - time: "35:47"
    title: "The Challenge: Handling Real-Time YouTube IP Blocking"
    description: "Understanding the IP blocking problem when scraping YouTube at scale"
  - time: "36:52"
    title: "The Solution: Implementing Proxies for Scraping"
    description: "Implementing residential proxies to handle IP blocking and rate limiting"
  - time: "47:09"
    title: "Intro to Temporal: Making Workflows Durable"
    description: "Introduction to Temporal for building durable, fault-tolerant workflows"
  - time: "58:25"
    title: "Migrating Logic into Temporal Activities"
    description: "Refactoring ingestion logic into Temporal activities for reliability"
  - time: "1:06:49"
    title: "Defining the Ingestion Workflow"
    description: "Defining the complete ingestion workflow with Temporal workflow definitions"
  - time: "1:16:43"
    title: "Implementing the Temporal Worker"
    description: "Building the Temporal worker to execute workflows and activities"
  - time: "1:29:08"
    title: "Part 2: Building the Research Agent with PydanticAI"
    description: "Starting Part 2: Building the research agent using PydanticAI framework"
  - time: "1:39:29"
    title: "Configuring Agent Instructions & Models"
    description: "Setting up agent instructions, prompts, and model configuration"
  - time: "1:46:37"
    title: "Optimization: Adding a Summarization Agent"
    description: "Adding a secondary summarization agent to handle long contexts effectively"
  - time: "1:57:34"
    title: "Converting the Script to a Durable Temporal Agent"
    description: "Migrating the research agent to use Temporal for durability and reliability"
  - time: "2:10:00"
    title: "Running the Full Durable Research Agent"
    description: "Executing the complete system: durable ingestion + research agent"
  - time: "2:19:45"
    title: "Final Results & Next Steps"
    description: "Reviewing results, key takeaways, and next steps for production deployment"
materials:
  - title: "Related Course: AI Engineering Buildcamp"
    url: "https://maven.com/alexey-grigorev/from-rag-to-agents"
    type: "article"
coreTools:
  - "YouTube Transcript API"
  - "ElasticSearch"
  - "Docker"
  - "Temporal"
  - "PydanticAI"
  - "OpenAI API"
learningObjectives:
  - "Building a durable data ingestion pipeline"
  - "Handling IP blocking and retries with proxies"
  - "Indexing long-form text into ElasticSearch"
  - "Designing a multi-stage research agent with tool use and summarization"
  - "Orchestrating workflows with Temporal"
  - "Handling retries, state, and recovery in production"
  - "Working with long contexts effectively"
outcome: "A production-oriented deep research agent that can answer questions using years of podcast transcripts, backed by a fault-tolerant ingestion workflow"
relatedCourse: "AI Engineering Buildcamp: From RAG to Agents"
---

This workshop walks through building a deep research agent over several years of DataTalks.Club podcast transcripts, with a deliberate focus on ingestion reliability and system design rather than model tuning. It covers extracting transcripts from YouTube, handling IP blocking with residential proxies, cleaning and structuring text, and indexing it in Elasticsearch, then orchestrating the entire pipeline with Temporal to handle retries, state, and recovery in a robust way. On top of this data layer, the workshop shows how to build a research agent with PydanticAI that combines search, retrieval, and a secondary summarization step to work effectively with long contexts in a production-oriented setup.

## Workshop Overview

Learn how to build a production-ready AI agent system with focus on reliability, fault tolerance, and system design. This workshop emphasizes robust data pipelines and orchestration over model tuning.

## What You'll Learn

- **Data Engineering**: Building durable ingestion pipelines
- **IP Management**: Handling blocking with residential proxies
- **Search Infrastructure**: Indexing long-form content in ElasticSearch
- **Workflow Orchestration**: Using Temporal for reliable execution
- **Agent Design**: Multi-stage research agents with tool use
- **Production Patterns**: Retries, state management, and recovery

## Core Tools and Technologies

- YouTube Transcript API for data extraction
- ElasticSearch for search and indexing
- Temporal for workflow orchestration
- Docker for containerization
- PydanticAI for agent framework
- OpenAI API for LLM capabilities

## Workshop Structure

### Part 1: Data Ingestion
Build a durable pipeline for extracting and processing YouTube transcripts.

### Part 2: Handling Challenges
- IP blocking detection and mitigation
- Residential proxy integration
- Retry logic and backoff strategies

### Part 3: Data Processing
- Text cleaning and structuring
- Chunking strategies for long content
- Metadata extraction

### Part 4: Search Infrastructure
- ElasticSearch setup and configuration
- Indexing strategies for long-form text
- Query optimization

### Part 5: Temporal Orchestration
- Workflow definition
- Activity implementation
- State management
- Retry and recovery patterns

### Part 6: Research Agent
- Multi-stage agent design
- Search and retrieval tools
- Summarization for long contexts
- Production-oriented patterns

## Key Focus Areas

- **Reliability**: Fault-tolerant ingestion workflows
- **Scalability**: Handling years of transcript data
- **Recovery**: Automatic retry and state recovery
- **System Design**: Production-oriented architecture
- **Agent Architecture**: Multi-stage research workflows

## Expected Outcome

By the end of this workshop, you'll have:
- A production-ready data ingestion pipeline
- Fault-tolerant workflow orchestration with Temporal
- ElasticSearch-indexed knowledge base
- A deep research agent that can answer questions using years of transcripts
- Understanding of production system design patterns

## Prerequisites

- Advanced Python skills
- Understanding of data engineering concepts
- Familiarity with workflow orchestration
- Knowledge of search systems (ElasticSearch)
- Experience with production system design

## Production Considerations

This workshop focuses on:
- **Reliability over Performance**: Ensuring data integrity
- **System Design over Model Tuning**: Architecture patterns
- **Fault Tolerance**: Handling failures gracefully
- **Scalability**: Processing large volumes of data
- **Maintainability**: Clean, production-ready code

## Related Course

This advanced workshop complements the **AI Engineering Buildcamp: From RAG to Agents** course, focusing on production system design and reliability.
