import Link from "next/link"
import { ArrowRight, Calendar, Video, Play } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getAllResources } from "@/lib/resources"

export const metadata = {
  title: "Event Recordings | AI Shipping Labs",
  description: "Workshop recordings, videos, and learning materials on building AI agents and practical systems.",
}

export default async function ResourcesPage() {
  const resources = await getAllResources()

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <section className="py-16 lg:py-24">
          <div className="mx-auto max-w-4xl px-6 lg:px-8">
            <div className="mb-12">
              <p className="text-sm font-medium uppercase tracking-widest text-accent">Event Recordings</p>
              <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
                Workshops & Learning Materials
              </h1>
              <p className="mt-4 text-lg text-muted-foreground">
                Workshop recordings with embedded content, timestamps, descriptions, and materials. Learn from hands-on sessions on building AI agents and practical systems.
              </p>
            </div>

            {resources.length === 0 ? (
              <div className="rounded-lg border border-border bg-card p-12 text-center">
                <Video className="mx-auto h-12 w-12 text-muted-foreground" />
                <p className="mt-4 text-lg text-muted-foreground">
                  No resources yet. Check back soon for workshops and learning materials.
                </p>
              </div>
            ) : (
              <div className="space-y-8">
                {resources.map((resource) => (
                  <article
                    key={resource.slug}
                    className="group rounded-lg border border-border bg-card p-6 transition-colors hover:border-accent/50"
                  >
                    <Link href={`/event-recordings/${resource.slug}`}>
                      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-2">
                            <Video className="h-5 w-5 text-accent" />
                            <h2 className="text-xl font-semibold text-foreground group-hover:text-accent transition-colors">
                              {resource.title}
                            </h2>
                          </div>
                          <p className="mt-2 text-muted-foreground line-clamp-2">
                            {resource.description}
                          </p>
                          <div className="mt-4 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                            <span className="flex items-center gap-1.5">
                              <Calendar className="h-4 w-4" />
                              {new Date(resource.date).toLocaleDateString("en-US", {
                                year: "numeric",
                                month: "long",
                                day: "numeric",
                              })}
                            </span>
                          </div>
                          {resource.tags && resource.tags.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {resource.tags.map((tag) => (
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
