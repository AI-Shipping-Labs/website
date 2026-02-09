import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, Calendar, Clock, Rocket, Code } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getProjectBySlug, getAllProjects } from "@/lib/projects"

interface PageProps {
  params: Promise<{ slug: string }>
}

export async function generateStaticParams() {
  const projects = await getAllProjects()
  // Static export requires at least one path; use placeholder when empty
  if (projects.length === 0) return [{ slug: "_" }]
  return projects.map((project) => ({ slug: project.slug }))
}

export async function generateMetadata({ params }: PageProps) {
  const { slug } = await params
  const project = await getProjectBySlug(slug)
  
  if (!project) {
    return { title: "Project Idea Not Found" }
  }
  
  return {
    title: `${project.title} | AI Engineering Lab`,
    description: project.description,
  }
}

export default async function ProjectPage({ params }: PageProps) {
  const { slug } = await params
  const project = await getProjectBySlug(slug)

  if (!project) {
    if (slug === "_") {
      return (
        <>
          <Header />
          <main className="min-h-screen pt-24">
            <div className="mx-auto max-w-3xl px-6 py-24 lg:px-8">
              <Link
                href="/projects"
                className="mb-8 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                <ArrowLeft className="h-4 w-4" />
                Back to Project Ideas
              </Link>
              <p className="text-muted-foreground">Project ideas coming soon.</p>
            </div>
          </main>
          <Footer />
        </>
      )
    }
    notFound()
  }

  const getDifficultyColor = (difficulty?: string) => {
    switch (difficulty) {
      case "beginner":
        return "bg-green-500/20 text-green-400"
      case "intermediate":
        return "bg-yellow-500/20 text-yellow-400"
      case "advanced":
        return "bg-red-500/20 text-red-400"
      default:
        return "bg-secondary text-muted-foreground"
    }
  }

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <article className="py-16 lg:py-24">
          <div className="mx-auto max-w-3xl px-6 lg:px-8">
            <Link
              href="/projects"
              className="mb-8 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Project Ideas
            </Link>

            <header className="mb-12">
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
                <Rocket className="h-4 w-4" />
                Project Idea
              </div>
              <h1 className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl lg:text-5xl">
                {project.title}
              </h1>
              {project.author && (
                <p className="mt-2 text-lg text-muted-foreground">by {project.author}</p>
              )}
              {project.description && (
                <p className="mt-4 text-xl text-muted-foreground">
                  {project.description}
                </p>
              )}

              <div className="mt-6 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Calendar className="h-4 w-4" />
                  {new Date(project.date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </span>
                {project.readingTime && (
                  <span className="flex items-center gap-1.5">
                    <Clock className="h-4 w-4" />
                    {project.readingTime}
                  </span>
                )}
                {project.estimatedTime && (
                  <span className="flex items-center gap-1.5">
                    <Code className="h-4 w-4" />
                    {project.estimatedTime}
                  </span>
                )}
                {project.difficulty && (
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${getDifficultyColor(project.difficulty)}`}>
                    {project.difficulty}
                  </span>
                )}
              </div>

              {project.tags && project.tags.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {project.tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full bg-secondary px-3 py-1 text-xs text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </header>

            <div 
              className="prose prose-invert prose-lg max-w-none
                prose-headings:font-semibold prose-headings:tracking-tight
                prose-h2:text-2xl prose-h2:mt-12 prose-h2:mb-4
                prose-h3:text-xl prose-h3:mt-8 prose-h3:mb-3
                prose-p:text-muted-foreground prose-p:leading-relaxed
                prose-a:text-accent prose-a:no-underline hover:prose-a:underline
                prose-strong:text-foreground prose-strong:font-semibold
                prose-code:text-accent prose-code:bg-secondary prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:font-mono prose-code:text-sm
                prose-pre:bg-card prose-pre:border prose-pre:border-border prose-pre:rounded-lg
                prose-blockquote:border-l-accent prose-blockquote:text-muted-foreground prose-blockquote:italic
                prose-ul:text-muted-foreground prose-ol:text-muted-foreground
                prose-li:marker:text-accent
                prose-img:rounded-lg prose-img:border prose-img:border-border"
              dangerouslySetInnerHTML={{ __html: project.contentHtml }}
            />
          </div>
        </article>
      </main>
      <Footer />
    </>
  )
}
