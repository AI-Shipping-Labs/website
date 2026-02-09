import Link from "next/link"
import { ArrowRight, Video, Calendar } from "lucide-react"
import { getAllResources } from "@/lib/resources"

export async function ResourcesSection() {
  const resources = await getAllResources()
  const latestResources = resources.slice(0, 3)

  return (
    <section id="resources" className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
              <Video className="h-4 w-4" />
              Resources
            </p>
            <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
              Workshops & Learning Materials
            </h2>
            <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
              Links to Alexey's workshops with embedded content, timestamps, descriptions, and materials.
            </p>
          </div>
          <Link
            href="/resources"
            className="inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
          >
            View all resources
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        {latestResources.length === 0 ? (
          <div className="mt-12 rounded-lg border border-border bg-card p-8 text-center">
            <p className="text-muted-foreground">
              Resources coming soon. Check back for workshops and learning materials.
            </p>
          </div>
        ) : (
          <div className="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {latestResources.map((resource) => (
              <article
                key={resource.slug}
                className="group flex flex-col rounded-xl border border-border bg-card p-6 transition-colors hover:border-accent/50"
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <Video className="h-5 w-5 text-accent" />
                    <h3 className="text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
                      {resource.title}
                    </h3>
                  </div>
                  <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">
                    {resource.description}
                  </p>

                  <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <Calendar className="h-4 w-4" />
                      {new Date(resource.date).toLocaleDateString("en-US", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                  </div>
                  {resource.tags && resource.tags.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {resource.tags.slice(0, 2).map((tag) => (
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
                  href={`/resources/${resource.slug}`}
                  className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
                >
                  View resource
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
