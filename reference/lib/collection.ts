export type CollectionCategory = "tools" | "models" | "courses" | "other"

export interface CollectionItem {
  id: string
  title: string
  description: string
  url: string
  category: CollectionCategory
  /** Optional: display name for repo or source, e.g. "github.com/..." */
  source?: string
}

export const COLLECTION_CATEGORIES: Record<
  CollectionCategory,
  { label: string; description: string }
> = {
  tools: { label: "Tools", description: "GitHub repos, CLIs, and dev tools" },
  models: { label: "Models", description: "Model hubs, runtimes, and inference" },
  courses: { label: "Courses", description: "Courses and learning tracks" },
  other: { label: "Other", description: "Datasets, APIs, and more" },
}

/** Curated links to GitHub tools, models, courses, and other resources. */
export const COLLECTION_ITEMS: CollectionItem[] = [
  {
    id: "lovable",
    title: "Lovable",
    description: "AI-powered app builder. Design in the browser, export to GitHub.",
    url: "https://lovable.dev",
    category: "tools",
    source: "lovable.dev",
  },
  {
    id: "cursor",
    title: "Cursor",
    description: "AI-first code editor. Built on VS Code, with Copilot-style assistance.",
    url: "https://cursor.com",
    category: "tools",
    source: "cursor.com",
  },
  {
    id: "claude-code",
    title: "Claude Code",
    description: "Command-line coding agent. Scaffold and edit projects from natural language.",
    url: "https://claude.com/claude-code",
    category: "tools",
    source: "claude.com",
  },
  {
    id: "llm-course",
    title: "LLM Course by Maxime Labonne",
    description: "Large language model course. From basics to RAG, agents, and fine-tuning.",
    url: "https://github.com/mlabonne/llm-course",
    category: "courses",
    source: "GitHub",
  },
  {
    id: "awesome-ml",
    title: "Awesome ML",
    description: "Curated list of ML resources. Frameworks, papers, and tools.",
    url: "https://github.com/josephmisiti/awesome-machine-learning",
    category: "other",
    source: "GitHub",
  },
  {
    id: "pydantic-ai",
    title: "PydanticAI",
    description: "Structured AI agents in Python. Type-safe, testable agent workflows.",
    url: "https://github.com/pydantic/pydantic-ai",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "langchain",
    title: "LangChain",
    description: "Framework for LLM applications. Chains, agents, and integrations.",
    url: "https://github.com/langchain-ai/langchain",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "pal-mcp",
    title: "PAL MCP",
    description: "Provider-agnostic MCP server that turns your AI CLI or IDE into a coordinator for multiple models: spawn isolated sub-agents, run cross-model debates and code reviews, and hand off full context between models for planning and implementation.",
    url: "https://github.com/BeehiveInnovations/pal-mcp-server",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "agents-md",
    title: "AGENTS.md",
    description: "Open standard for AI coding agents: a consistent location for project setup, build steps, tests, and coding conventions. Used by 60,000+ open-source projects; keeps READMEs clean while improving agent reliability across tools.",
    url: "https://agents.md",
    category: "other",
    source: "agents.md",
  },
  {
    id: "promptify",
    title: "Promptify",
    description: "Developer-friendly NLP wrapper for LLMs. Run NER, classification, and more with minimal code and zero training data. Converts unstructured model output into reliable, structured Python objects for production.",
    url: "https://github.com/promptslab/Promptify",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "minimax-m1",
    title: "MiniMax-M1",
    description: "Open-weight reasoning model: large MoE backbone with hybrid attention, up to 1M-token context, less test-time compute than comparable models. Strong in software engineering and extended-input settings.",
    url: "https://github.com/MiniMax-AI/MiniMax-M1",
    category: "models",
    source: "GitHub",
  },
  {
    id: "collaborating-with-codex",
    title: "collaborating-with-codex",
    description: "Agent Skill that lets Claude delegate coding tasks to the OpenAI Codex CLI for multi-model collaboration. Claude coordinates and refines; Codex handles implementation, debugging, and code analysis in a sandbox.",
    url: "https://github.com/eddiearc/codex-delegator",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "happy-coder",
    title: "Happy Coder",
    description: "Mobile, web, and CLI client for Claude Code and Codex. Run and monitor from anywhere with E2E encryption, push notifications when the agent needs attention, and seamless switching between desktop and phone.",
    url: "https://github.com/slopus/happy",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "docker-sandboxes-claude-code",
    title: "Docker Sandboxes for Claude Code",
    description: "Run Claude Code in an isolated, reproducible Docker environment without changing how you use the CLI. Sandboxed file access and credentials for security and reliability; supports all Claude Code options.",
    url: "https://docs.docker.com/ai/sandboxes/claude-code/",
    category: "tools",
    source: "Docker Docs",
  },
  {
    id: "playwriter-mcp",
    title: "Playwriter MCP",
    description: "Lets AI agents control your Chrome browser via a lightweight extension using the full Playwright API with minimal context. Reliable browser automation: screenshots, flow validation, logged-in pages—no custom automation to maintain.",
    url: "https://github.com/remorses/playwriter",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "oh-my-claude-sisyphus",
    title: "oh-my-claude-sisyphus",
    description: "Claude Code plugin for native multi-agent orchestration: specialized subagents, hooks, and slash commands for parallel coding tasks. Automates delegation, search, planning, and “keep going until done” workflows with smart model routing.",
    url: "https://github.com/Yeachan-Heo/oh-my-claude-sisyphus",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "dlt-fundamentals",
    title: "dlt Fundamentals",
    description: "Course from dlthub on building robust ELT pipelines. Includes a holiday lesson (Dec 22) on integrating LLMs into your workflow, with 50 swag packs to compete for.",
    url: "https://dlthub.com/docs/tutorial/fundamentals-course",
    category: "courses",
    source: "dlthub.com",
  },
  {
    id: "ai-agents-email-crash-course",
    title: "AI Agents Email Crash-Course (Cohort Edition)",
    description: "Free cohort-based version running December and January. Complete the project and review three other submissions to receive a certificate of completion signed by Alexey.",
    url: "https://alexeygrigorev.com/courses.html",
    category: "courses",
    source: "alexeygrigorev.com",
  },
  {
    id: "claude-use-cases",
    title: "Claude Use Cases",
    description: "Curated library of real-world Claude use cases across research, writing, coding, analysis, and everyday work. Organized by role, industry, and feature with concrete, end-to-end examples.",
    url: "https://www.claude.com/resources/use-cases",
    category: "other",
    source: "claude.com",
  },
  {
    id: "ai-engineering-hub",
    title: "AI Engineering Hub",
    description: "Large open-source GitHub repo: 90+ production-ready projects, tutorials, and reference implementations for LLMs, RAG, agents, MCP, multimodal systems, and evaluation. Structured by difficulty and use case.",
    url: "https://github.com/patchy631/ai-engineering-hub",
    category: "other",
    source: "GitHub",
  },
  {
    id: "agentic-ai-crash-course",
    title: "Agentic AI Crash Course",
    description: "Free introductory crash course on how modern AI agents work in practice: tools, RAG, memory, planning, MCP, and multi-agent systems. Focus on real-world system design and limitations.",
    url: "https://www.deeplearning.ai/courses/agentic-ai/",
    category: "courses",
    source: "DeepLearning.AI",
  },
  {
    id: "cs146s-modern-software-developer",
    title: "Assignments for CS146S: The Modern Software Developer",
    description: "Programming assignments for Stanford's CS146S (Fall 2025): AI-assisted software development with modern tooling—LLM-based coding, testing, and documentation.",
    url: "https://github.com/mihail911/modern-software-dev-assignments",
    category: "courses",
    source: "GitHub",
  },
  {
    id: "llm-fine-tuning-roadmap",
    title: "LLM Fine-Tuning roadmap",
    description: "Curated resource for practitioners: core fine-tuning concepts, transformer internals, training infrastructure, data preparation, PEFT and alignment methods, and tools for training and deploying LLMs.",
    url: "https://github.com/Curated-Awesome-Lists/awesome-llms-fine-tuning",
    category: "courses",
    source: "GitHub",
  },
  {
    id: "claude-code-large-context-reasoning",
    title: "Claude Code and Large-Context Reasoning",
    description: "Materials from Tim Warner's O'Reilly Live Learning course: production-ready AI-assisted development with Claude Code, large-context reasoning, MCP-based memory, agents, and custom skills. Code review, automation, and CI/CD examples.",
    url: "https://learning.oreilly.com/live-events/",
    category: "courses",
    source: "O'Reilly",
  },
  {
    id: "awesome-slash",
    title: "awesome-slash",
    description: "Curated list of tools, patterns, and projects built around slash-command interfaces. Practical reference for command-driven workflows, bots, and developer tools.",
    url: "https://github.com/avifenesh/awesome-slash",
    category: "other",
    source: "GitHub",
  },
  {
    id: "astronomer-agents",
    title: "astronomer/agents",
    description: "Open-source agent skills for data engineering: 13 skills to extend and automate data workflows with AI agents.",
    url: "https://github.com/astronomer/agents",
    category: "tools",
    source: "GitHub",
  },
  {
    id: "500-ai-agents-projects",
    title: "500+ AI Agent Projects",
    description: "Curated collection of AI agent use cases across healthcare, finance, education, retail, and more. Maps practical applications to open-source implementations and frameworks (CrewAI, AutoGen, Agno, LangGraph). Hands-on inspiration hub for builders and practitioners.",
    url: "https://github.com/ashishpatel26/500-AI-Agents-Projects",
    category: "other",
    source: "GitHub",
  },
]

export function getCollectionByCategory(
  category: CollectionCategory | "all"
): CollectionItem[] {
  if (category === "all") return [...COLLECTION_ITEMS]
  return COLLECTION_ITEMS.filter((item) => item.category === category)
}

export function getAllCollectionCategories(): CollectionCategory[] {
  return ["tools", "models", "courses", "other"]
}
