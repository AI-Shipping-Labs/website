import Link from "next/link"
import { ArrowRight, BookOpen, Clock } from "lucide-react"
import { getAllTutorials } from "@/lib/tutorials"

export async function TutorialsSection() {
  const tutorials = await getAllTutorials()
  const latestTutorials = tutorials.slice(0, 3)

  return (
    <section id="tutorials" className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
              <BookOpen className="h-4 w-4" />
              Tutorials
            </p>
            <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
              Step-by-Step Guides
            </h2>
            <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
              Easy to read tutorials on narrow topics. Learn how to do this and that.
            </p>
          </div>
          <Link
            href="/tutorials"
            className="inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
          >
            View all tutorials
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        {latestTutorials.length === 0 ? (
          <div className="mt-12 rounded-lg border border-border bg-card p-8 text-center">
            <p className="text-muted-foreground">
              Tutorials coming soon. Check back for step-by-step guides.
            </p>
          </div>
        ) : (
          <div className="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {latestTutorials.map((tutorial) => (
              <article
                key={tutorial.slug}
                className="group flex flex-col rounded-xl border border-border bg-card p-6 transition-colors hover:border-accent/50"
              >
                <div className="flex-1">
                  <h3 className="mt-3 text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
                    {tutorial.title}
                  </h3>
                  <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">
                    {tutorial.description}
                  </p>

                  <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    {tutorial.readingTime && (
                      <span className="inline-flex items-center gap-1.5">
                        <Clock className="h-4 w-4" />
                        {tutorial.readingTime}
                      </span>
                    )}
                  </div>
                  {tutorial.tags && tutorial.tags.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {tutorial.tags.slice(0, 2).map((tag) => (
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
                  href={`/tutorials/${tutorial.slug}`}
                  className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
                >
                  Read tutorial
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
