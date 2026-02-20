"""
Management command to seed the database with realistic sample data for development.

Creates users across all tiers, articles, courses with modules and units,
cohorts with enrollments, events, recordings, projects, curated links,
downloads, polls with votes, notifications, and newsletter subscribers.

Idempotent: running twice does not create duplicates.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from content.models import (
    Article, Course, Module, Unit, Cohort, CohortEnrollment,
    Recording, Project, CuratedLink, Download,
)
from email_app.models import NewsletterSubscriber
from events.models import Event, EventRegistration
from notifications.models import Notification
from payments.models import Tier
from voting.models import Poll, PollOption, PollVote


User = get_user_model()

now = timezone.now()
today = now.date()


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------
TIERS = [
    {
        'slug': 'free', 'name': 'Free', 'level': 0,
        'description': 'Browse free articles, projects, and recordings.',
        'features': ['Public blog posts', 'Community projects', 'Free recordings'],
    },
    {
        'slug': 'basic', 'name': 'Basic', 'level': 10,
        'price_eur_month': 9, 'price_eur_year': 90,
        'description': 'Access basic-tier content and downloads.',
        'features': ['Everything in Free', 'Basic articles', 'Downloadable resources'],
    },
    {
        'slug': 'main', 'name': 'Main', 'level': 20,
        'price_eur_month': 29, 'price_eur_year': 290,
        'description': 'Full access to courses, events, and community polls.',
        'features': ['Everything in Basic', 'All courses', 'Live events', 'Topic voting'],
    },
    {
        'slug': 'premium', 'name': 'Premium', 'level': 30,
        'price_eur_month': 79, 'price_eur_year': 790,
        'description': 'Premium content, cohort courses, and course voting.',
        'features': ['Everything in Main', 'Cohort courses', 'Course voting', 'Priority support'],
    },
]

# ---------------------------------------------------------------------------
# User definitions
# ---------------------------------------------------------------------------
USERS = [
    {
        'email': 'admin@aishippinglabs.com',
        'password': 'admin123',
        'first_name': 'Admin',
        'last_name': 'User',
        'is_superuser': True,
        'is_staff': True,
        'tier_slug': 'premium',
    },
    {
        'email': 'free@test.com',
        'password': 'testpass123',
        'first_name': 'Freya',
        'last_name': 'Freeman',
        'tier_slug': 'free',
    },
    {
        'email': 'basic@test.com',
        'password': 'testpass123',
        'first_name': 'Bob',
        'last_name': 'Baker',
        'tier_slug': 'basic',
    },
    {
        'email': 'main@test.com',
        'password': 'testpass123',
        'first_name': 'Maria',
        'last_name': 'Martinez',
        'tier_slug': 'main',
    },
    {
        'email': 'premium@test.com',
        'password': 'testpass123',
        'first_name': 'Pete',
        'last_name': 'Preston',
        'tier_slug': 'premium',
    },
    {
        'email': 'alice@test.com',
        'password': 'testpass123',
        'first_name': 'Alice',
        'last_name': 'Anderson',
        'tier_slug': 'main',
    },
    {
        'email': 'charlie@test.com',
        'password': 'testpass123',
        'first_name': 'Charlie',
        'last_name': 'Chen',
        'tier_slug': 'basic',
    },
    {
        'email': 'diana@test.com',
        'password': 'testpass123',
        'first_name': 'Diana',
        'last_name': 'Davis',
        'tier_slug': 'free',
    },
]

# ---------------------------------------------------------------------------
# Article definitions
# ---------------------------------------------------------------------------
ARTICLES = [
    {
        'slug': 'getting-started-with-llm-agents',
        'title': 'Getting Started with LLM Agents',
        'description': 'A practical introduction to building LLM-powered agents with Python.',
        'content_markdown': (
            '# Getting Started with LLM Agents\n\n'
            'Large language model agents combine the reasoning power of LLMs with '
            'tool use to accomplish complex tasks autonomously.\n\n'
            '## What is an LLM Agent?\n\n'
            'An LLM agent is a program that uses a language model as its core '
            'reasoning engine. It can plan, use tools, and iterate to solve problems.\n\n'
            '## Building Your First Agent\n\n'
            '```python\nfrom pydantic_ai import Agent\n\n'
            'agent = Agent("openai:gpt-4o", system_prompt="You are a helpful assistant.")\n'
            'result = agent.run_sync("What is the weather today?")\n'
            'print(result.data)\n```\n\n'
            '## Key Components\n\n'
            '- **Planning**: Breaking down tasks into steps\n'
            '- **Tool use**: Calling APIs, reading files, running code\n'
            '- **Memory**: Keeping track of conversation context\n'
            '- **Reflection**: Evaluating and improving outputs\n\n'
            'Start with a simple agent and gradually add capabilities as needed.'
        ),
        'author': 'Alexey Grigorev',
        'tags': ['agents', 'llm', 'python', 'getting-started'],
        'cover_image_url': 'https://picsum.photos/seed/agents/800/400',
        'date_offset': -30,
        'required_level': 0,
    },
    {
        'slug': 'rag-pipeline-best-practices',
        'title': 'RAG Pipeline Best Practices for Production',
        'description': 'Lessons learned from deploying retrieval-augmented generation systems at scale.',
        'content_markdown': (
            '# RAG Pipeline Best Practices\n\n'
            'Retrieval-Augmented Generation (RAG) combines search with generation '
            'to produce grounded, factual answers.\n\n'
            '## Chunking Strategies\n\n'
            'The most important decision in your RAG pipeline is how you chunk documents. '
            'Overlapping chunks of 512 tokens with 50 token overlap work well as a starting point.\n\n'
            '## Embedding Models\n\n'
            'Use domain-specific embedding models when available. For general use, '
            '`text-embedding-3-small` offers a good balance of quality and cost.\n\n'
            '## Retrieval Quality\n\n'
            'Always evaluate retrieval quality separately from generation quality. '
            'Use metrics like Recall@K and MRR to track retrieval performance.\n\n'
            '## Reranking\n\n'
            'Add a reranking step after initial retrieval to improve precision. '
            'Cross-encoder models like `bge-reranker-v2` can significantly boost results.\n\n'
            '```python\n# Example: Cohere reranking\nimport cohere\n\n'
            'co = cohere.Client(api_key)\nreranked = co.rerank(\n'
            '    query="How to deploy ML models?",\n'
            '    documents=retrieved_docs,\n    top_n=5\n)\n```'
        ),
        'author': 'Alexey Grigorev',
        'tags': ['rag', 'llm', 'production', 'embeddings'],
        'cover_image_url': 'https://picsum.photos/seed/rag/800/400',
        'date_offset': -25,
        'required_level': 0,
    },
    {
        'slug': 'fine-tuning-llms-on-custom-data',
        'title': 'Fine-Tuning LLMs on Your Custom Data',
        'description': 'A step-by-step guide to fine-tuning open-source LLMs with LoRA.',
        'content_markdown': (
            '# Fine-Tuning LLMs on Custom Data\n\n'
            'Fine-tuning adapts a pre-trained model to your specific domain or task.\n\n'
            '## When to Fine-Tune\n\n'
            '- Your domain has specialized terminology\n'
            '- You need consistent output formatting\n'
            '- RAG alone does not capture the nuance of your domain\n\n'
            '## LoRA: Efficient Fine-Tuning\n\n'
            'Low-Rank Adaptation (LoRA) lets you fine-tune large models on consumer hardware '
            'by training only small adapter matrices.\n\n'
            '```python\nfrom peft import LoraConfig, get_peft_model\n\n'
            'config = LoraConfig(r=16, lora_alpha=32, target_modules=["q_proj", "v_proj"])\n'
            'model = get_peft_model(base_model, config)\n```\n\n'
            '## Data Preparation\n\n'
            'Quality matters more than quantity. Start with 500-1000 high-quality examples '
            'in instruction-response format.'
        ),
        'author': 'Maria Martinez',
        'tags': ['fine-tuning', 'llm', 'lora', 'training'],
        'cover_image_url': 'https://picsum.photos/seed/finetune/800/400',
        'date_offset': -20,
        'required_level': 10,
    },
    {
        'slug': 'building-mcp-servers-for-ai-tools',
        'title': 'Building MCP Servers for AI Tools',
        'description': 'How to create Model Context Protocol servers that extend AI capabilities.',
        'content_markdown': (
            '# Building MCP Servers\n\n'
            'The Model Context Protocol (MCP) provides a standard way for AI models '
            'to interact with external tools and data sources.\n\n'
            '## MCP Architecture\n\n'
            'An MCP server exposes tools and resources that AI clients can discover '
            'and invoke. The protocol uses JSON-RPC over stdio or HTTP.\n\n'
            '## Creating a Tool\n\n'
            '```python\nfrom mcp.server import Server\nfrom mcp.types import Tool\n\n'
            'server = Server("my-tools")\n\n'
            '@server.tool()\nasync def search_docs(query: str) -> str:\n'
            '    """Search the documentation."""\n'
            '    results = await vector_search(query)\n'
            '    return format_results(results)\n```\n\n'
            '## Best Practices\n\n'
            '- Keep tool descriptions clear and specific\n'
            '- Return structured data when possible\n'
            '- Handle errors gracefully with informative messages\n'
            '- Test tools independently before connecting to an AI client'
        ),
        'author': 'Alexey Grigorev',
        'tags': ['mcp', 'tools', 'ai-agents', 'protocol'],
        'cover_image_url': 'https://picsum.photos/seed/mcp/800/400',
        'date_offset': -15,
        'required_level': 0,
    },
    {
        'slug': 'deploying-ml-models-with-docker',
        'title': 'Deploying ML Models with Docker and FastAPI',
        'description': 'Production-ready ML model serving with Docker containers and FastAPI.',
        'content_markdown': (
            '# Deploying ML Models with Docker\n\n'
            'Containerized deployments provide reproducibility, scalability, and isolation '
            'for your machine learning models.\n\n'
            '## The Deployment Stack\n\n'
            '- **FastAPI**: High-performance async API framework\n'
            '- **Docker**: Containerization for consistent environments\n'
            '- **ONNX Runtime**: Optimized model inference\n\n'
            '## Dockerfile\n\n'
            '```dockerfile\nFROM python:3.12-slim\n'
            'COPY requirements.txt .\nRUN pip install -r requirements.txt\n'
            'COPY app/ /app/\nCMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]\n```\n\n'
            '## Health Checks\n\n'
            'Always include health check endpoints:\n\n'
            '```python\n@app.get("/health")\nasync def health():\n'
            '    return {"status": "healthy", "model_loaded": model is not None}\n```\n\n'
            'Monitor latency percentiles, not just averages.'
        ),
        'author': 'Bob Baker',
        'tags': ['deployment', 'docker', 'fastapi', 'mlops'],
        'cover_image_url': 'https://picsum.photos/seed/docker/800/400',
        'date_offset': -10,
        'required_level': 10,
    },
    {
        'slug': 'prompt-engineering-patterns',
        'title': 'Prompt Engineering Patterns That Actually Work',
        'description': 'Reliable prompt engineering techniques backed by empirical testing.',
        'content_markdown': (
            '# Prompt Engineering Patterns\n\n'
            'Effective prompts are structured, specific, and tested.\n\n'
            '## Chain of Thought\n\n'
            'Ask the model to think step by step. This improves accuracy on reasoning tasks '
            'by 10-30% in most benchmarks.\n\n'
            '## Few-Shot Examples\n\n'
            'Provide 3-5 examples of the desired input-output format. '
            'Choose diverse examples that cover edge cases.\n\n'
            '## Output Formatting\n\n'
            'Request structured output (JSON, XML) with a clear schema:\n\n'
            '```\nRespond in JSON with this schema:\n'
            '{"answer": "string", "confidence": 0-1, "sources": ["string"]}\n```\n\n'
            '## System Prompts\n\n'
            'Use the system prompt for role, constraints, and context. '
            'Use the user prompt for the specific task.\n\n'
            '## Testing\n\n'
            'Build an evaluation set of 50+ examples and test every prompt change against it.'
        ),
        'author': 'Alexey Grigorev',
        'tags': ['prompt-engineering', 'llm', 'best-practices'],
        'cover_image_url': 'https://picsum.photos/seed/prompts/800/400',
        'date_offset': -5,
        'required_level': 0,
    },
    {
        'slug': 'ai-agent-evaluation-strategies',
        'title': 'Evaluating AI Agents: Metrics and Strategies',
        'description': 'How to measure and improve AI agent performance in real-world applications.',
        'content_markdown': (
            '# Evaluating AI Agents\n\n'
            'Evaluation is the most underinvested area of AI agent development.\n\n'
            '## Task Completion Rate\n\n'
            'The most important metric: did the agent complete the task correctly? '
            'Binary pass/fail for each test case.\n\n'
            '## Cost and Latency\n\n'
            'Track tokens used and wall-clock time per task. '
            'An agent that is correct but takes 10 minutes and costs $5 per query is not practical.\n\n'
            '## Evaluation Framework\n\n'
            '```python\ndef evaluate_agent(agent, test_cases):\n'
            '    results = []\n    for case in test_cases:\n'
            '        output = agent.run(case["input"])\n'
            '        score = check_output(output, case["expected"])\n'
            '        results.append({"case": case["name"], "score": score})\n'
            '    return results\n```\n\n'
            '## Continuous Evaluation\n\n'
            'Run evals on every code change. Treat evaluation sets like test suites.'
        ),
        'author': 'Pete Preston',
        'tags': ['evaluation', 'agents', 'metrics', 'testing'],
        'cover_image_url': 'https://picsum.photos/seed/evals/800/400',
        'date_offset': -2,
        'required_level': 20,
    },
]

# ---------------------------------------------------------------------------
# Course definitions
# ---------------------------------------------------------------------------
COURSES = [
    {
        'slug': 'llm-agents-fundamentals',
        'title': 'LLM Agents Fundamentals',
        'description': (
            'Learn to build production-ready AI agents from scratch. '
            'Covers tool use, memory, planning, and multi-agent systems.'
        ),
        'instructor_name': 'Alexey Grigorev',
        'instructor_bio': 'ML engineer and founder of AI Shipping Labs.',
        'cover_image_url': 'https://picsum.photos/seed/course-agents/800/400',
        'required_level': 0,
        'status': 'published',
        'is_free': True,
        'tags': ['agents', 'llm', 'python'],
        'modules': [
            {
                'title': 'Introduction to AI Agents',
                'sort_order': 0,
                'units': [
                    {
                        'title': 'What Are LLM Agents?',
                        'sort_order': 0,
                        'video_url': 'https://www.youtube.com/watch?v=example1',
                        'body': 'An overview of what LLM agents are and why they matter.',
                        'is_preview': True,
                        'timestamps': [
                            {'time_seconds': 0, 'label': 'Introduction'},
                            {'time_seconds': 120, 'label': 'What is an agent?'},
                            {'time_seconds': 300, 'label': 'Agent architecture'},
                        ],
                    },
                    {
                        'title': 'Setting Up Your Environment',
                        'sort_order': 1,
                        'body': (
                            'Install Python 3.12, set up a virtual environment, '
                            'and install the required libraries.'
                        ),
                        'homework': 'Create a virtual environment and install pydantic-ai.',
                    },
                ],
            },
            {
                'title': 'Tool Use and Function Calling',
                'sort_order': 1,
                'units': [
                    {
                        'title': 'Defining Tools for Your Agent',
                        'sort_order': 0,
                        'video_url': 'https://www.youtube.com/watch?v=example2',
                        'body': 'How to define and register tools that your agent can call.',
                    },
                    {
                        'title': 'Structured Output with Pydantic',
                        'sort_order': 1,
                        'body': 'Use Pydantic models to validate and parse agent outputs.',
                        'homework': 'Build an agent that returns structured JSON using Pydantic.',
                    },
                ],
            },
        ],
    },
    {
        'slug': 'rag-in-production',
        'title': 'RAG in Production',
        'description': (
            'Build and deploy production-grade RAG pipelines. '
            'Covers chunking, embeddings, retrieval, reranking, and evaluation.'
        ),
        'instructor_name': 'Alexey Grigorev',
        'instructor_bio': 'ML engineer and founder of AI Shipping Labs.',
        'cover_image_url': 'https://picsum.photos/seed/course-rag/800/400',
        'required_level': 20,
        'status': 'published',
        'is_free': False,
        'tags': ['rag', 'production', 'embeddings'],
        'modules': [
            {
                'title': 'RAG Fundamentals',
                'sort_order': 0,
                'units': [
                    {
                        'title': 'RAG Architecture Overview',
                        'sort_order': 0,
                        'video_url': 'https://www.youtube.com/watch?v=example3',
                        'body': 'The core components of a RAG pipeline and how they fit together.',
                        'is_preview': True,
                    },
                    {
                        'title': 'Document Processing and Chunking',
                        'sort_order': 1,
                        'body': (
                            'Strategies for splitting documents into chunks that preserve meaning.'
                        ),
                        'homework': (
                            'Implement three chunking strategies and compare retrieval quality.'
                        ),
                    },
                ],
            },
            {
                'title': 'Advanced Retrieval',
                'sort_order': 1,
                'units': [
                    {
                        'title': 'Hybrid Search: Dense + Sparse',
                        'sort_order': 0,
                        'body': 'Combine BM25 keyword search with dense vector retrieval.',
                    },
                    {
                        'title': 'Reranking for Precision',
                        'sort_order': 1,
                        'video_url': 'https://www.youtube.com/watch?v=example4',
                        'body': 'Use cross-encoder models to rerank retrieved passages.',
                    },
                ],
            },
        ],
    },
    {
        'slug': 'mlops-with-docker-and-kubernetes',
        'title': 'MLOps with Docker and Kubernetes',
        'description': (
            'Deploy, monitor, and scale ML models in production. '
            'Hands-on with Docker, Kubernetes, and CI/CD pipelines.'
        ),
        'instructor_name': 'Bob Baker',
        'instructor_bio': 'DevOps engineer specializing in ML infrastructure.',
        'cover_image_url': 'https://picsum.photos/seed/course-mlops/800/400',
        'required_level': 10,
        'status': 'published',
        'is_free': False,
        'tags': ['mlops', 'docker', 'kubernetes', 'deployment'],
        'modules': [
            {
                'title': 'Containerizing ML Models',
                'sort_order': 0,
                'units': [
                    {
                        'title': 'Docker for Data Scientists',
                        'sort_order': 0,
                        'body': 'Learn Docker fundamentals from an ML perspective.',
                        'is_preview': True,
                    },
                    {
                        'title': 'Multi-Stage Builds for ML',
                        'sort_order': 1,
                        'body': 'Optimize image size with multi-stage builds.',
                        'homework': 'Create a Dockerfile for a scikit-learn model server.',
                    },
                ],
            },
        ],
    },
]

# ---------------------------------------------------------------------------
# Event definitions
# ---------------------------------------------------------------------------
EVENTS = [
    {
        'slug': 'llm-agents-workshop-march',
        'title': 'LLM Agents Workshop: Building Your First Agent',
        'description': (
            'Hands-on workshop where we build an LLM agent from scratch. '
            'Bring your laptop and an API key.'
        ),
        'event_type': 'live',
        'status': 'upcoming',
        'start_offset_days': 14,
        'duration_hours': 2,
        'location': 'Zoom',
        'tags': ['agents', 'workshop', 'hands-on'],
        'required_level': 0,
        'max_participants': 50,
    },
    {
        'slug': 'rag-deep-dive-live',
        'title': 'RAG Deep Dive: Chunking and Retrieval',
        'description': (
            'Live session exploring advanced chunking strategies, hybrid retrieval, '
            'and practical tips for production RAG.'
        ),
        'event_type': 'live',
        'status': 'upcoming',
        'start_offset_days': 28,
        'duration_hours': 1.5,
        'location': 'Zoom',
        'tags': ['rag', 'retrieval', 'deep-dive'],
        'required_level': 20,
    },
    {
        'slug': 'community-demo-day-feb',
        'title': 'Community Demo Day: February Projects',
        'description': (
            'Community members showcase their AI projects. '
            'Five-minute demos followed by Q&A.'
        ),
        'event_type': 'live',
        'status': 'live',
        'start_offset_days': 0,
        'duration_hours': 1,
        'location': 'Zoom',
        'tags': ['community', 'demo', 'showcase'],
        'required_level': 10,
    },
    {
        'slug': 'fine-tuning-masterclass-jan',
        'title': 'Fine-Tuning Masterclass: LoRA and QLoRA',
        'description': (
            'Hands-on masterclass covering LoRA, QLoRA, and data preparation '
            'for fine-tuning open-source models.'
        ),
        'event_type': 'live',
        'status': 'completed',
        'start_offset_days': -14,
        'duration_hours': 2,
        'location': 'Zoom',
        'tags': ['fine-tuning', 'lora', 'masterclass'],
        'required_level': 20,
    },
    {
        'slug': 'prompt-engineering-async',
        'title': 'Async Challenge: Prompt Engineering Tournament',
        'description': (
            'Week-long async challenge: solve 10 prompt engineering puzzles. '
            'Leaderboard and prizes for top scorers.'
        ),
        'event_type': 'async',
        'status': 'completed',
        'start_offset_days': -30,
        'duration_hours': 168,
        'location': 'GitHub',
        'tags': ['prompt-engineering', 'challenge', 'async'],
        'required_level': 0,
    },
]

# ---------------------------------------------------------------------------
# Recording definitions
# ---------------------------------------------------------------------------
RECORDINGS = [
    {
        'slug': 'fine-tuning-masterclass-recording',
        'title': 'Fine-Tuning Masterclass: LoRA and QLoRA (Recording)',
        'description': 'Recording of the January fine-tuning masterclass.',
        'date_offset': -13,
        'tags': ['fine-tuning', 'lora', 'recording'],
        'youtube_url': 'https://www.youtube.com/watch?v=rec_example1',
        'required_level': 20,
        'event_slug': 'fine-tuning-masterclass-jan',
        'timestamps': [
            {'time_seconds': 0, 'label': 'Introduction'},
            {'time_seconds': 600, 'label': 'LoRA explained'},
            {'time_seconds': 1800, 'label': 'QLoRA walkthrough'},
            {'time_seconds': 3600, 'label': 'Q&A'},
        ],
    },
    {
        'slug': 'prompt-tournament-highlights',
        'title': 'Prompt Engineering Tournament: Highlights and Solutions',
        'description': 'Recap of the prompt engineering async challenge with winning solutions.',
        'date_offset': -28,
        'tags': ['prompt-engineering', 'challenge', 'recording'],
        'youtube_url': 'https://www.youtube.com/watch?v=rec_example2',
        'required_level': 0,
        'event_slug': 'prompt-engineering-async',
    },
    {
        'slug': 'intro-to-mcp-talk',
        'title': 'Introduction to Model Context Protocol',
        'description': 'Community talk on MCP: what it is, how to build servers, and real-world use cases.',
        'date_offset': -45,
        'tags': ['mcp', 'tools', 'talk'],
        'youtube_url': 'https://www.youtube.com/watch?v=rec_example3',
        'required_level': 0,
    },
    {
        'slug': 'rag-evaluation-workshop-recording',
        'title': 'RAG Evaluation Workshop Recording',
        'description': 'Deep dive into evaluating RAG systems with practical metrics and tools.',
        'date_offset': -60,
        'tags': ['rag', 'evaluation', 'workshop'],
        'youtube_url': 'https://www.youtube.com/watch?v=rec_example4',
        'required_level': 10,
    },
    {
        'slug': 'docker-for-ml-intro',
        'title': 'Docker for ML Engineers: Getting Started',
        'description': 'Beginner-friendly introduction to Docker aimed at data scientists and ML engineers.',
        'date_offset': -75,
        'tags': ['docker', 'mlops', 'beginner'],
        'youtube_url': 'https://www.youtube.com/watch?v=rec_example5',
        'required_level': 0,
    },
    {
        'slug': 'multi-agent-systems-talk',
        'title': 'Multi-Agent Systems: Patterns and Pitfalls',
        'description': 'Lessons learned from building multi-agent systems in production.',
        'date_offset': -90,
        'tags': ['agents', 'multi-agent', 'production'],
        'youtube_url': 'https://www.youtube.com/watch?v=rec_example6',
        'required_level': 20,
    },
]

# ---------------------------------------------------------------------------
# Project definitions
# ---------------------------------------------------------------------------
PROJECTS = [
    {
        'slug': 'slack-bot-with-rag',
        'title': 'Build a Slack Bot with RAG',
        'description': 'Create a Slack bot that answers questions from your documentation using RAG.',
        'content_markdown': (
            '# Slack Bot with RAG\n\n'
            'Build a Slack bot that indexes your team documentation and answers questions '
            'using retrieval-augmented generation.\n\n'
            '## Requirements\n\n'
            '- Slack app with bot token\n'
            '- Vector database (Qdrant or Chroma)\n'
            '- OpenAI or local LLM\n\n'
            '## Steps\n\n'
            '1. Set up Slack event subscriptions\n'
            '2. Index documentation into vector store\n'
            '3. Build retrieval pipeline\n'
            '4. Connect to Slack message handler'
        ),
        'difficulty': 'intermediate',
        'estimated_time': '8-12 hours',
        'tags': ['rag', 'slack', 'bot', 'python'],
        'required_level': 0,
        'date_offset': -20,
    },
    {
        'slug': 'ai-code-reviewer',
        'title': 'AI Code Reviewer GitHub Action',
        'description': 'Build a GitHub Action that reviews pull requests using an LLM.',
        'content_markdown': (
            '# AI Code Reviewer\n\n'
            'Create a GitHub Action that automatically reviews pull requests '
            'and provides feedback on code quality, security, and best practices.\n\n'
            '## Architecture\n\n'
            '- GitHub Action triggered on PR events\n'
            '- Diff parsing and context extraction\n'
            '- LLM review with structured output\n'
            '- PR comment posting via GitHub API'
        ),
        'difficulty': 'advanced',
        'estimated_time': '15-20 hours',
        'tags': ['github', 'code-review', 'agents', 'ci-cd'],
        'required_level': 10,
        'date_offset': -15,
        'source_code_url': 'https://github.com/example/ai-code-reviewer',
    },
    {
        'slug': 'sentiment-analysis-dashboard',
        'title': 'Real-Time Sentiment Analysis Dashboard',
        'description': 'Stream social media mentions and classify sentiment in real time.',
        'content_markdown': (
            '# Sentiment Analysis Dashboard\n\n'
            'Build a dashboard that monitors social media and classifies '
            'mention sentiment using a fine-tuned model.\n\n'
            '## Stack\n\n'
            '- Streamlit for the dashboard\n'
            '- Hugging Face transformers for classification\n'
            '- Redis for real-time data streaming'
        ),
        'difficulty': 'beginner',
        'estimated_time': '4-6 hours',
        'tags': ['nlp', 'sentiment', 'dashboard', 'streamlit'],
        'required_level': 0,
        'date_offset': -40,
        'submitter_email': 'alice@test.com',
    },
    {
        'slug': 'document-extraction-pipeline',
        'title': 'Document Extraction Pipeline with Vision LLMs',
        'description': 'Extract structured data from invoices and receipts using vision language models.',
        'content_markdown': (
            '# Document Extraction Pipeline\n\n'
            'Use GPT-4V or Claude to extract structured data from documents. '
            'Build a pipeline that processes PDFs, images, and scanned documents.\n\n'
            '## Features\n\n'
            '- Multi-format input (PDF, PNG, JPEG)\n'
            '- Schema-driven extraction with Pydantic\n'
            '- Confidence scoring and human-in-the-loop'
        ),
        'difficulty': 'intermediate',
        'estimated_time': '10-15 hours',
        'tags': ['vision', 'extraction', 'llm', 'documents'],
        'required_level': 20,
        'date_offset': -10,
    },
    {
        'slug': 'local-llm-chatbot',
        'title': 'Local LLM Chatbot with Ollama',
        'description': 'Run a chatbot entirely on your machine using Ollama and a local model.',
        'content_markdown': (
            '# Local LLM Chatbot\n\n'
            'Build a chatbot that runs 100% locally using Ollama. '
            'No API keys, no cloud, complete privacy.\n\n'
            '## Getting Started\n\n'
            '1. Install Ollama\n'
            '2. Pull a model: `ollama pull llama3.2`\n'
            '3. Build the chat interface with Gradio'
        ),
        'difficulty': 'beginner',
        'estimated_time': '2-3 hours',
        'tags': ['local', 'ollama', 'chatbot', 'privacy'],
        'required_level': 0,
        'date_offset': -5,
        'submitter_email': 'charlie@test.com',
    },
]

# ---------------------------------------------------------------------------
# Curated link definitions
# ---------------------------------------------------------------------------
CURATED_LINKS = [
    {
        'item_id': 'seed-pydantic-ai',
        'title': 'PydanticAI',
        'description': 'Structured AI agents in Python with type-safe tool definitions.',
        'url': 'https://github.com/pydantic/pydantic-ai',
        'category': 'tools',
        'tags': ['agents', 'python', 'pydantic'],
    },
    {
        'item_id': 'seed-ollama',
        'title': 'Ollama',
        'description': 'Run large language models locally. Supports Llama, Mistral, Gemma, and more.',
        'url': 'https://ollama.com',
        'category': 'tools',
        'tags': ['local', 'inference', 'models'],
    },
    {
        'item_id': 'seed-qdrant',
        'title': 'Qdrant',
        'description': 'High-performance vector database for similarity search and RAG.',
        'url': 'https://qdrant.tech',
        'category': 'tools',
        'tags': ['vector-db', 'rag', 'search'],
    },
    {
        'item_id': 'seed-llama3',
        'title': 'Llama 3',
        'description': 'Meta open-weight LLM family. Strong performance across reasoning and coding tasks.',
        'url': 'https://llama.meta.com',
        'category': 'models',
        'tags': ['llm', 'open-source', 'meta'],
    },
    {
        'item_id': 'seed-mistral',
        'title': 'Mistral AI Models',
        'description': 'Efficient open-weight models. Mistral and Mixtral for various use cases.',
        'url': 'https://mistral.ai',
        'category': 'models',
        'tags': ['llm', 'open-source', 'efficient'],
    },
    {
        'item_id': 'seed-ml-zoomcamp',
        'title': 'ML Zoomcamp',
        'description': 'Free machine learning course covering regression, classification, deep learning, and deployment.',
        'url': 'https://mlzoomcamp.com',
        'category': 'courses',
        'tags': ['ml', 'course', 'free'],
    },
    {
        'item_id': 'seed-huggingface',
        'title': 'Hugging Face',
        'description': 'The platform for ML. Models, datasets, Spaces, and the transformers library.',
        'url': 'https://huggingface.co',
        'category': 'other',
        'tags': ['platform', 'models', 'datasets'],
    },
    {
        'item_id': 'seed-langsmith',
        'title': 'LangSmith',
        'description': 'Observability and testing platform for LLM applications. Trace, debug, and evaluate.',
        'url': 'https://smith.langchain.com',
        'category': 'tools',
        'tags': ['observability', 'testing', 'langchain'],
    },
    {
        'item_id': 'seed-awesome-llm-apps',
        'title': 'Awesome LLM Apps',
        'description': 'Curated list of LLM application examples across various domains.',
        'url': 'https://github.com/Shubhamsaboo/awesome-llm-apps',
        'category': 'other',
        'tags': ['curated', 'examples', 'llm'],
    },
    {
        'item_id': 'seed-deeplearning-ai',
        'title': 'DeepLearning.AI Short Courses',
        'description': 'Free short courses on LLMs, RAG, agents, and fine-tuning from Andrew Ng.',
        'url': 'https://www.deeplearning.ai/short-courses/',
        'category': 'courses',
        'tags': ['courses', 'free', 'llm'],
    },
]

# ---------------------------------------------------------------------------
# Download definitions
# ---------------------------------------------------------------------------
DOWNLOADS = [
    {
        'slug': 'llm-agents-cheatsheet',
        'title': 'LLM Agents Cheat Sheet',
        'description': 'One-page reference for building LLM agents: patterns, tools, and prompts.',
        'file_url': 'https://storage.example.com/downloads/llm-agents-cheatsheet.pdf',
        'file_type': 'pdf',
        'file_size_bytes': 524288,
        'tags': ['agents', 'cheatsheet', 'reference'],
        'required_level': 0,
    },
    {
        'slug': 'rag-evaluation-notebook',
        'title': 'RAG Evaluation Jupyter Notebook',
        'description': 'Complete notebook for evaluating RAG pipelines with sample data and metrics.',
        'file_url': 'https://storage.example.com/downloads/rag-evaluation.ipynb',
        'file_type': 'notebook',
        'file_size_bytes': 1048576,
        'tags': ['rag', 'evaluation', 'notebook'],
        'required_level': 10,
    },
    {
        'slug': 'mlops-docker-templates',
        'title': 'MLOps Docker Templates',
        'description': 'Production-ready Dockerfiles and docker-compose configs for common ML serving patterns.',
        'file_url': 'https://storage.example.com/downloads/mlops-docker-templates.zip',
        'file_type': 'zip',
        'file_size_bytes': 2097152,
        'tags': ['docker', 'mlops', 'templates'],
        'required_level': 20,
    },
    {
        'slug': 'prompt-engineering-slides',
        'title': 'Prompt Engineering Workshop Slides',
        'description': 'Slide deck from the prompt engineering workshop with examples and exercises.',
        'file_url': 'https://storage.example.com/downloads/prompt-engineering-slides.pdf',
        'file_type': 'slides',
        'file_size_bytes': 3145728,
        'tags': ['prompt-engineering', 'slides', 'workshop'],
        'required_level': 0,
    },
]

# ---------------------------------------------------------------------------
# Poll definitions
# ---------------------------------------------------------------------------
POLLS = [
    {
        'title': 'What topic should our next deep-dive cover?',
        'description': 'Vote for the topic you want to see in our next deep-dive session.',
        'poll_type': 'topic',
        'status': 'open',
        'allow_proposals': True,
        'max_votes_per_user': 2,
        'options': [
            {'title': 'Advanced RAG: GraphRAG and Knowledge Graphs', 'description': 'Explore graph-based retrieval approaches.'},
            {'title': 'LLM Security: Prompt Injection and Defenses', 'description': 'Security patterns for LLM applications.'},
            {'title': 'Building Multi-Modal Agents', 'description': 'Agents that process text, images, and audio.'},
            {'title': 'AI-Assisted Code Review at Scale', 'description': 'How to set up AI code review in your CI/CD pipeline.'},
        ],
    },
    {
        'title': 'Which mini-course should we create next?',
        'description': 'Premium members: vote on our next mini-course.',
        'poll_type': 'course',
        'status': 'open',
        'allow_proposals': False,
        'max_votes_per_user': 1,
        'options': [
            {'title': 'Fine-Tuning with Unsloth', 'description': 'Efficient fine-tuning with the Unsloth library.'},
            {'title': 'Building MCP Servers in Python', 'description': 'Hands-on course on the Model Context Protocol.'},
            {'title': 'Evaluation-Driven AI Development', 'description': 'Build better AI apps by writing evals first.'},
        ],
    },
]

# ---------------------------------------------------------------------------
# Newsletter subscriber definitions
# ---------------------------------------------------------------------------
NEWSLETTER_SUBSCRIBERS = [
    'newsletter1@test.com',
    'newsletter2@test.com',
    'newsletter3@test.com',
    'newsletter4@test.com',
    'newsletter5@test.com',
]


class Command(BaseCommand):
    help = 'Seed the database with realistic sample data for development'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Clear existing data before seeding.',
        )

    def handle(self, *args, **options):
        if options['flush']:
            self._flush()

        summary = {}
        summary['tiers'] = self._seed_tiers()
        summary['users'] = self._seed_users()
        summary['articles'] = self._seed_articles()
        summary['courses'] = self._seed_courses()
        summary['cohorts'] = self._seed_cohorts()
        summary['events'] = self._seed_events()
        summary['recordings'] = self._seed_recordings()
        summary['projects'] = self._seed_projects()
        summary['curated_links'] = self._seed_curated_links()
        summary['downloads'] = self._seed_downloads()
        summary['polls'] = self._seed_polls()
        summary['notifications'] = self._seed_notifications()
        summary['newsletter_subscribers'] = self._seed_newsletter_subscribers()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Seed data created successfully.'))
        self.stdout.write('')
        self.stdout.write('Summary:')
        for key, count in summary.items():
            label = key.replace('_', ' ').title()
            self.stdout.write(f'  {label}: {count}')

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------
    def _flush(self):
        self.stdout.write('Flushing existing data...')
        PollVote.objects.all().delete()
        PollOption.objects.all().delete()
        Poll.objects.all().delete()
        Notification.objects.all().delete()
        EventRegistration.objects.all().delete()
        CohortEnrollment.objects.all().delete()
        Cohort.objects.all().delete()
        Recording.objects.all().delete()
        Event.objects.all().delete()
        Unit.objects.all().delete()
        Module.objects.all().delete()
        Course.objects.all().delete()
        Article.objects.all().delete()
        Project.objects.all().delete()
        CuratedLink.objects.filter(item_id__startswith='seed-').delete()
        Download.objects.all().delete()
        NewsletterSubscriber.objects.all().delete()
        User.objects.filter(email__in=[u['email'] for u in USERS]).delete()
        self.stdout.write('  Flushed.')

    # ------------------------------------------------------------------
    # Tiers
    # ------------------------------------------------------------------
    def _seed_tiers(self):
        count = 0
        for tier_data in TIERS:
            _, created = Tier.objects.get_or_create(
                slug=tier_data['slug'],
                defaults={
                    'name': tier_data['name'],
                    'level': tier_data['level'],
                    'price_eur_month': tier_data.get('price_eur_month'),
                    'price_eur_year': tier_data.get('price_eur_year'),
                    'description': tier_data.get('description', ''),
                    'features': tier_data.get('features', []),
                },
            )
            if created:
                count += 1
        self.stdout.write(f'  Tiers: {count} created')
        return count

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def _seed_users(self):
        count = 0
        for user_data in USERS:
            email = user_data['email']
            if User.objects.filter(email=email).exists():
                continue
            tier = Tier.objects.get(slug=user_data['tier_slug'])
            is_super = user_data.get('is_superuser', False)
            if is_super:
                user = User.objects.create_superuser(
                    email=email,
                    password=user_data['password'],
                    first_name=user_data.get('first_name', ''),
                    last_name=user_data.get('last_name', ''),
                )
            else:
                user = User.objects.create_user(
                    email=email,
                    password=user_data['password'],
                    first_name=user_data.get('first_name', ''),
                    last_name=user_data.get('last_name', ''),
                )
            user.tier = tier
            user.email_verified = True
            user.save()
            count += 1
        self.stdout.write(f'  Users: {count} created')
        return count

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------
    def _seed_articles(self):
        count = 0
        for article_data in ARTICLES:
            _, created = Article.objects.get_or_create(
                slug=article_data['slug'],
                defaults={
                    'title': article_data['title'],
                    'description': article_data['description'],
                    'content_markdown': article_data['content_markdown'],
                    'author': article_data['author'],
                    'tags': article_data['tags'],
                    'cover_image_url': article_data.get('cover_image_url', ''),
                    'date': today + timedelta(days=article_data['date_offset']),
                    'required_level': article_data.get('required_level', 0),
                    'published': True,
                },
            )
            if created:
                count += 1
        self.stdout.write(f'  Articles: {count} created')
        return count

    # ------------------------------------------------------------------
    # Courses with modules and units
    # ------------------------------------------------------------------
    def _seed_courses(self):
        count = 0
        for course_data in COURSES:
            course, created = Course.objects.get_or_create(
                slug=course_data['slug'],
                defaults={
                    'title': course_data['title'],
                    'description': course_data['description'],
                    'instructor_name': course_data['instructor_name'],
                    'instructor_bio': course_data.get('instructor_bio', ''),
                    'cover_image_url': course_data.get('cover_image_url', ''),
                    'required_level': course_data.get('required_level', 0),
                    'status': course_data.get('status', 'published'),
                    'is_free': course_data.get('is_free', False),
                    'tags': course_data.get('tags', []),
                },
            )
            if created:
                count += 1
                for module_data in course_data.get('modules', []):
                    module, _ = Module.objects.get_or_create(
                        course=course,
                        title=module_data['title'],
                        defaults={'sort_order': module_data['sort_order']},
                    )
                    for unit_data in module_data.get('units', []):
                        Unit.objects.get_or_create(
                            module=module,
                            title=unit_data['title'],
                            defaults={
                                'sort_order': unit_data.get('sort_order', 0),
                                'video_url': unit_data.get('video_url', ''),
                                'body': unit_data.get('body', ''),
                                'homework': unit_data.get('homework', ''),
                                'timestamps': unit_data.get('timestamps', []),
                                'is_preview': unit_data.get('is_preview', False),
                            },
                        )
        self.stdout.write(f'  Courses: {count} created')
        return count

    # ------------------------------------------------------------------
    # Cohorts
    # ------------------------------------------------------------------
    def _seed_cohorts(self):
        count = 0
        # Cohort for the RAG course
        rag_course = Course.objects.filter(slug='rag-in-production').first()
        if rag_course:
            cohort, created = Cohort.objects.get_or_create(
                course=rag_course,
                name='March 2026 Cohort',
                defaults={
                    'start_date': today + timedelta(days=10),
                    'end_date': today + timedelta(days=40),
                    'is_active': True,
                    'max_participants': 30,
                },
            )
            if created:
                count += 1
                # Enroll some users
                for email in ['main@test.com', 'premium@test.com', 'alice@test.com']:
                    user = User.objects.filter(email=email).first()
                    if user:
                        CohortEnrollment.objects.get_or_create(
                            cohort=cohort, user=user,
                        )

        # Cohort for MLOps course
        mlops_course = Course.objects.filter(slug='mlops-with-docker-and-kubernetes').first()
        if mlops_course:
            cohort2, created = Cohort.objects.get_or_create(
                course=mlops_course,
                name='April 2026 Cohort',
                defaults={
                    'start_date': today + timedelta(days=45),
                    'end_date': today + timedelta(days=75),
                    'is_active': True,
                    'max_participants': 25,
                },
            )
            if created:
                count += 1
                for email in ['basic@test.com', 'main@test.com']:
                    user = User.objects.filter(email=email).first()
                    if user:
                        CohortEnrollment.objects.get_or_create(
                            cohort=cohort2, user=user,
                        )

        self.stdout.write(f'  Cohorts: {count} created')
        return count

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _seed_events(self):
        count = 0
        for event_data in EVENTS:
            start = now + timedelta(days=event_data['start_offset_days'])
            end = start + timedelta(hours=event_data.get('duration_hours', 1))
            _, created = Event.objects.get_or_create(
                slug=event_data['slug'],
                defaults={
                    'title': event_data['title'],
                    'description': event_data['description'],
                    'event_type': event_data['event_type'],
                    'status': event_data['status'],
                    'start_datetime': start,
                    'end_datetime': end,
                    'location': event_data.get('location', ''),
                    'tags': event_data.get('tags', []),
                    'required_level': event_data.get('required_level', 0),
                    'max_participants': event_data.get('max_participants'),
                },
            )
            if created:
                count += 1
                # Register some users for upcoming/live events
                if event_data['status'] in ('upcoming', 'live'):
                    event = Event.objects.get(slug=event_data['slug'])
                    for email in ['main@test.com', 'premium@test.com', 'alice@test.com']:
                        user = User.objects.filter(email=email).first()
                        if user:
                            EventRegistration.objects.get_or_create(
                                event=event, user=user,
                            )
        self.stdout.write(f'  Events: {count} created')
        return count

    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------
    def _seed_recordings(self):
        count = 0
        for rec_data in RECORDINGS:
            defaults = {
                'title': rec_data['title'],
                'description': rec_data['description'],
                'date': today + timedelta(days=rec_data['date_offset']),
                'tags': rec_data.get('tags', []),
                'youtube_url': rec_data.get('youtube_url', ''),
                'required_level': rec_data.get('required_level', 0),
                'timestamps': rec_data.get('timestamps', []),
                'published': True,
            }
            # Link to event if specified
            event_slug = rec_data.get('event_slug')
            if event_slug:
                event = Event.objects.filter(slug=event_slug).first()
                if event:
                    defaults['event'] = event

            _, created = Recording.objects.get_or_create(
                slug=rec_data['slug'],
                defaults=defaults,
            )
            if created:
                count += 1
        self.stdout.write(f'  Recordings: {count} created')
        return count

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    def _seed_projects(self):
        count = 0
        for proj_data in PROJECTS:
            defaults = {
                'title': proj_data['title'],
                'description': proj_data['description'],
                'content_markdown': proj_data.get('content_markdown', ''),
                'difficulty': proj_data.get('difficulty', ''),
                'estimated_time': proj_data.get('estimated_time', ''),
                'tags': proj_data.get('tags', []),
                'required_level': proj_data.get('required_level', 0),
                'date': today + timedelta(days=proj_data['date_offset']),
                'source_code_url': proj_data.get('source_code_url', ''),
                'published': True,
            }
            # Link submitter if specified
            submitter_email = proj_data.get('submitter_email')
            if submitter_email:
                submitter = User.objects.filter(email=submitter_email).first()
                if submitter:
                    defaults['submitter'] = submitter

            _, created = Project.objects.get_or_create(
                slug=proj_data['slug'],
                defaults=defaults,
            )
            if created:
                count += 1
        self.stdout.write(f'  Projects: {count} created')
        return count

    # ------------------------------------------------------------------
    # Curated links
    # ------------------------------------------------------------------
    def _seed_curated_links(self):
        count = 0
        for idx, link_data in enumerate(CURATED_LINKS):
            _, created = CuratedLink.objects.get_or_create(
                item_id=link_data['item_id'],
                defaults={
                    'title': link_data['title'],
                    'description': link_data['description'],
                    'url': link_data['url'],
                    'category': link_data['category'],
                    'tags': link_data.get('tags', []),
                    'sort_order': idx,
                    'published': True,
                },
            )
            if created:
                count += 1
        self.stdout.write(f'  Curated links: {count} created')
        return count

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------
    def _seed_downloads(self):
        count = 0
        for dl_data in DOWNLOADS:
            _, created = Download.objects.get_or_create(
                slug=dl_data['slug'],
                defaults={
                    'title': dl_data['title'],
                    'description': dl_data['description'],
                    'file_url': dl_data['file_url'],
                    'file_type': dl_data['file_type'],
                    'file_size_bytes': dl_data.get('file_size_bytes', 0),
                    'tags': dl_data.get('tags', []),
                    'required_level': dl_data.get('required_level', 0),
                    'published': True,
                },
            )
            if created:
                count += 1
        self.stdout.write(f'  Downloads: {count} created')
        return count

    # ------------------------------------------------------------------
    # Polls
    # ------------------------------------------------------------------
    def _seed_polls(self):
        count = 0
        for poll_data in POLLS:
            poll, created = Poll.objects.get_or_create(
                title=poll_data['title'],
                defaults={
                    'description': poll_data['description'],
                    'poll_type': poll_data['poll_type'],
                    'status': poll_data['status'],
                    'allow_proposals': poll_data.get('allow_proposals', False),
                    'max_votes_per_user': poll_data.get('max_votes_per_user', 3),
                },
            )
            if created:
                count += 1
                options = []
                for opt_data in poll_data.get('options', []):
                    option, _ = PollOption.objects.get_or_create(
                        poll=poll,
                        title=opt_data['title'],
                        defaults={
                            'description': opt_data.get('description', ''),
                        },
                    )
                    options.append(option)

                # Add some votes from users
                voters = User.objects.filter(
                    email__in=['main@test.com', 'premium@test.com', 'alice@test.com'],
                )
                for voter in voters:
                    # Each voter votes on 1-2 options
                    for option in options[:poll_data.get('max_votes_per_user', 1)]:
                        PollVote.objects.get_or_create(
                            poll=poll, option=option, user=voter,
                        )
        self.stdout.write(f'  Polls: {count} created')
        return count

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _seed_notifications(self):
        count = 0
        notification_data = [
            {
                'email': 'main@test.com',
                'title': 'New article: Prompt Engineering Patterns',
                'body': 'A new article on prompt engineering patterns has been published.',
                'url': '/blog/prompt-engineering-patterns',
                'notification_type': 'new_content',
            },
            {
                'email': 'premium@test.com',
                'title': 'LLM Agents Workshop in 24 hours',
                'body': 'Reminder: the LLM Agents Workshop starts tomorrow.',
                'url': '/events/llm-agents-workshop-march',
                'notification_type': 'event_reminder',
            },
            {
                'email': 'main@test.com',
                'title': 'New course available: RAG in Production',
                'body': 'A new course on production RAG pipelines is now available.',
                'url': '/courses/rag-in-production',
                'notification_type': 'new_content',
            },
            {
                'email': 'alice@test.com',
                'title': 'Community Demo Day is live!',
                'body': 'The February Community Demo Day is starting now. Join the Zoom call.',
                'url': '/events/community-demo-day-feb',
                'notification_type': 'event_reminder',
            },
            {
                'email': 'free@test.com',
                'title': 'Welcome to AI Shipping Labs!',
                'body': 'Thanks for joining. Check out our free courses and articles.',
                'url': '/',
                'notification_type': 'announcement',
            },
        ]
        for notif_data in notification_data:
            user = User.objects.filter(email=notif_data['email']).first()
            if user:
                _, created = Notification.objects.get_or_create(
                    user=user,
                    title=notif_data['title'],
                    defaults={
                        'body': notif_data['body'],
                        'url': notif_data['url'],
                        'notification_type': notif_data['notification_type'],
                    },
                )
                if created:
                    count += 1
        self.stdout.write(f'  Notifications: {count} created')
        return count

    # ------------------------------------------------------------------
    # Newsletter subscribers
    # ------------------------------------------------------------------
    def _seed_newsletter_subscribers(self):
        count = 0
        for email in NEWSLETTER_SUBSCRIBERS:
            _, created = NewsletterSubscriber.objects.get_or_create(
                email=email,
                defaults={'is_active': True},
            )
            if created:
                count += 1
        self.stdout.write(f'  Newsletter subscribers: {count} created')
        return count
