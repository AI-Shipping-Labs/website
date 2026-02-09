import Link from "next/link"
import { ArrowRight, Calendar, Clock, Rocket, Code } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getAllProjects } from "@/lib/projects"

export const metadata = {
  title: "Project Ideas | AI Engineering Lab",
  description: "Pet and portfolio project ideas to build. From small side projects to portfolio-worthy systems, expanded from Alexey's newsletter.",
}

export default async function ProjectsPage() {
  const projects = await getAllProjects()

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
        <section className="py-16 lg:py-24">
          <div className="mx-auto max-w-4xl px-6 lg:px-8">
            <div className="mb-12">
              <p className="text-sm font-medium uppercase tracking-widest text-accent">Project Ideas</p>
              <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
                Pet & Portfolio Project Ideas
              </h1>
              <p className="mt-4 text-lg text-muted-foreground">
                Ideas for things to build—from small pet projects to portfolio-worthy systems. 
                Expanded from Alexey's newsletter; each idea is a complete system you can build and learn from.
              </p>
            </div>

            {projects.length === 0 ? (
              <div className="rounded-lg border border-border bg-card p-12 text-center">
                <Rocket className="mx-auto h-12 w-12 text-muted-foreground" />
                <p className="mt-4 text-lg text-muted-foreground">
                  No project ideas yet. Check back soon for pet and portfolio project ideas.
                </p>
              </div>
            ) : (
              <div className="space-y-8">
                {projects.map((project) => (
                  <article
                    key={project.slug}
                    className="group rounded-lg border border-border bg-card p-6 transition-colors hover:border-accent/50"
                  >
                    <Link href={`/projects/${project.slug}`}>
                      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-3 mb-2">
                            <h2 className="text-xl font-semibold text-foreground group-hover:text-accent transition-colors">
                              {project.title}
                            </h2>
                            {project.difficulty && (
                              <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${getDifficultyColor(project.difficulty)}`}>
                                {project.difficulty}
                              </span>
                            )}
                          </div>
                          <p className="mt-2 text-muted-foreground line-clamp-2">
                            {project.description}
                          </p>
                          <div className="mt-4 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
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
                          </div>
                          {project.tags && project.tags.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {project.tags.map((tag) => (
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
                        <ArrowRight className="h-5 w-5 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-accent" />
                      </div>
                    </Link>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      </main>
      <Footer />
    </>
  )
}
