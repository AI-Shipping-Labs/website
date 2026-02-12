import Link from "next/link"
import { ArrowLeft, Video, FolderKanban, ArrowRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"

export const metadata = {
  title: "Topics & Content | AI Shipping Labs",
  description: "Explore the workshops, live streams, and projects covered in AI Shipping Labs community. From AI agents to MLOps, production ML to deployment.",
}

const workshops = [
  {
    title: "Coding Agent with Skills and Commands",
    tags: ["AI Agents", "Python"],
  },
  {
    title: "Guardrails for AI Agents",
    tags: ["AI Safety", "Production"],
  },
  {
    title: "Building AI Agents with MCP, PydanticAI and OpenAI",
    tags: ["AI Agents", "MCP"],
  },
  {
    title: "Build a Production-Ready YouTube AI Agent with Temporal",
    tags: ["AI Agents", "Temporal", "Production"],
  },
  {
    title: "Docker for Data Engineering: Postgres, Docker Compose, and Real-World Workflows",
    tags: ["Docker", "Data Engineering"],
  },
  {
    title: "Deep Learning with PyTorch: Build, Train and Deploy an Image Classifier",
    tags: ["Deep Learning", "PyTorch"],
  },
  {
    title: "Kubernetes Tutorial: Deploy Machine Learning Models with Docker and FastAPI",
    tags: ["Kubernetes", "MLOps", "Deployment"],
  },
  {
    title: "Deploy Machine Learning Models with FastAPI, Docker, and Fly.io",
    tags: ["Deployment", "FastAPI"],
  },
  {
    title: "Deploy Machine Learning Models with AWS Lambda (Serverless) and ONNX",
    tags: ["Serverless", "AWS", "ONNX"],
  },
  {
    title: "AI Coding Tools Compared: ChatGPT, Claude, Copilot, Cursor, Lovable and AI Agents",
    tags: ["AI Tools", "Productivity"],
  },
  {
    title: "Build a Django Coding Agent with OpenAI Tools",
    tags: ["AI Agents", "Django"],
  },
  {
    title: "Building AI Agents with Function Calling and RAG",
    tags: ["AI Agents", "RAG"],
  },
  {
    title: "Build LLM Agents with Function Calling in Python",
    tags: ["LLMs", "AI Agents"],
  },
  {
    title: "Implement a Search Engine",
    tags: ["Search", "Information Retrieval"],
  },
  {
    title: "Build a Fully Automated AI Podcast with Horror Stories from Images",
    tags: ["AI", "Automation", "Creative"],
  },
  {
    title: "Lightweight MLOps Zoomcamp",
    tags: ["MLOps", "Production"],
  },
]

const projects = [
  {
    title: "Data Version Control (DVC)",
    description: "Learn to version control your data and ML models alongside your code",
  },
  {
    title: "Docker",
    description: "Containerize ML applications for consistent development and deployment",
  },
  {
    title: "Python Package",
    description: "Structure and distribute your ML code as a proper Python package",
  },
  {
    title: "Rust",
    description: "Explore Rust for performance-critical ML components",
  },
  {
    title: "Julia",
    description: "High-performance computing for data science and ML",
  },
  {
    title: "GitHub Actions (Parts 1 & 2)",
    description: "Automate ML workflows with CI/CD pipelines",
  },
  {
    title: "FastAPI",
    description: "Build production-ready APIs for ML model serving",
  },
  {
    title: "Recommendation Systems (Parts 1 & 2)",
    description: "Design and implement recommendation engines from scratch",
  },
  {
    title: "Clustering Algorithms and Models",
    description: "Implement and apply clustering for real-world use cases",
  },
]

export default function TopicsPage() {
  return (
    <main className="min-h-screen">
      <Header />

      <section className="px-6 pt-32 pb-16 lg:px-8 lg:pt-40 lg:pb-24">
        <div className="mx-auto max-w-5xl">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground mb-8"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>

          <div className="max-w-3xl">
            <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Topics & Content</h1>
            <p className="mt-4 text-lg text-muted-foreground leading-relaxed">
              A preview of the workshops, live streams, and hands-on projects covered in AI Shipping Labs.
              Content focuses on practical, production-grade AI and ML systems — not theory for theory's sake.
            </p>
          </div>

          <div className="mt-16">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent">
                <Video className="h-5 w-5 text-accent-foreground" />
              </div>
              <h2 className="text-xl font-semibold">Workshops & Live Streams</h2>
            </div>
            <p className="mt-4 text-muted-foreground max-w-2xl">
              Deep-dive sessions on specific topics. Community members can propose and vote on upcoming workshops.
            </p>

            <div className="mt-8 grid gap-4 sm:grid-cols-2">
              {workshops.map((workshop) => (
                <div
                  key={workshop.title}
                  className="rounded-xl border border-border bg-card p-5 transition-colors hover:border-border/80"
                >
                  <h3 className="font-medium leading-snug">{workshop.title}</h3>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {workshop.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full bg-secondary px-2.5 py-0.5 text-xs text-muted-foreground"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-20">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent">
                <FolderKanban className="h-5 w-5 text-accent-foreground" />
              </div>
              <h2 className="text-xl font-semibold">Project of the Week</h2>
            </div>
            <p className="mt-4 text-muted-foreground max-w-2xl">
              Focused, time-boxed projects where community members build together.
              Learn by doing, share your work, get feedback.
            </p>

            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {projects.map((project) => (
                <div
                  key={project.title}
                  className="rounded-xl border border-border bg-card p-5"
                >
                  <h3 className="font-medium">{project.title}</h3>
                  <p className="mt-2 text-sm text-muted-foreground">{project.description}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-20 rounded-2xl border border-border bg-card p-8 lg:p-12">
            <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
              <div>
                <h2 className="text-xl font-semibold">Content is shaped by members</h2>
                <p className="mt-2 text-muted-foreground max-w-xl">
                  Tier 2 and Tier 3 members can propose topics, vote on what gets covered next,
                  and influence the direction of live streams and workshops.
                </p>
              </div>
              <Button asChild className="bg-accent text-accent-foreground hover:bg-accent/90 flex-shrink-0">
                <Link href="/#tiers">
                  View Membership Tiers
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            </div>
          </div>

          <div className="mt-20 border-t border-border pt-12">
            <h2 className="text-xl font-semibold">Content Philosophy</h2>
            <div className="mt-6 grid gap-8 sm:grid-cols-3">
              <div>
                <h3 className="font-medium text-accent">Production-First</h3>
                <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                  Focus on what actually works in production, not just what looks good in notebooks.
                </p>
              </div>
              <div>
                <h3 className="font-medium text-accent">Practitioner-Led</h3>
                <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                  Content from someone actively building, not just teaching. Real problems, real solutions.
                </p>
              </div>
              <div>
                <h3 className="font-medium text-accent">Learning by Building</h3>
                <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                  Every topic includes hands-on work. The goal is skills you can use Monday morning.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <Footer />
    </main>
  )
}
