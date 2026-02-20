import Link from "next/link"
import { ArrowRight, Rocket, Clock, Code } from "lucide-react"
import { getAllProjects } from "@/lib/projects"

export async function ProjectsSection() {
  const projects = await getAllProjects()
  const latestProjects = projects.slice(0, 3)

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
    <section id="projects" className="border-t border-border bg-card py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
              <Rocket className="h-4 w-4" />
              Project Ideas
            </p>
            <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
              Pet & Portfolio Project Ideas
            </h2>
            <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
              Project ideas and real projects from people who've taken courses. End-to-end AI applications and agentic workflows you can learn from and build on.
            </p>
          </div>
          <Link
            href="/projects"
            className="inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
          >
            View all project ideas
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        {latestProjects.length === 0 ? (
          <div className="mt-12 rounded-lg border border-border bg-background p-8 text-center">
            <p className="text-muted-foreground">
              Project ideas coming soon. Check back for pet and portfolio project ideas.
            </p>
          </div>
        ) : (
          <div className="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {latestProjects.map((project) => (
              <article
                key={project.slug}
                className="group flex flex-col rounded-xl border border-border bg-background p-6 transition-colors hover:border-accent/50"
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
                      {project.title}
                    </h3>
                    {project.author && (
                      <span className="text-xs text-muted-foreground">by {project.author}</span>
                    )}
                    {project.difficulty && (
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${getDifficultyColor(project.difficulty)}`}>
                        {project.difficulty}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">
                    {project.description}
                  </p>

                  <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    {project.readingTime && (
                      <span className="inline-flex items-center gap-1.5">
                        <Clock className="h-4 w-4" />
                        {project.readingTime}
                      </span>
                    )}
                    {project.estimatedTime && (
                      <span className="inline-flex items-center gap-1.5">
                        <Code className="h-4 w-4" />
                        {project.estimatedTime}
                      </span>
                    )}
                  </div>
                  {project.tags && project.tags.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {project.tags.slice(0, 2).map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full bg-secondary px-2.5 py-0.5 text-xs text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <Link
                  href={`/projects/${project.slug}`}
                  className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
                >
                  View idea
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </Link>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
