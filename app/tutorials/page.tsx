import Link from "next/link"
import { ArrowRight, Calendar, Clock, BookOpen } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getAllTutorials } from "@/lib/tutorials"

export const metadata = {
  title: "Tutorials | AI Engineering Lab",
  description: "Easy to read tutorials on narrow topics. Learn how to do this and that.",
}

export default async function TutorialsPage() {
  const tutorials = await getAllTutorials()

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <section className="py-16 lg:py-24">
          <div className="mx-auto max-w-4xl px-6 lg:px-8">
            <div className="mb-12">
              <p className="text-sm font-medium uppercase tracking-widest text-accent">Tutorials</p>
              <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
                Step-by-Step Guides
              </h1>
              <p className="mt-4 text-lg text-muted-foreground">
                Easy to read tutorials on narrow topics. Learn how to do this and that.
              </p>
            </div>

            {tutorials.length === 0 ? (
              <div className="rounded-lg border border-border bg-card p-12 text-center">
                <BookOpen className="mx-auto h-12 w-12 text-muted-foreground" />
                <p className="mt-4 text-lg text-muted-foreground">
                  No tutorials yet. Check back soon for step-by-step guides.
                </p>
              </div>
            ) : (
              <div className="space-y-8">
                {tutorials.map((tutorial) => (
                  <article
                    key={tutorial.slug}
                    className="group rounded-lg border border-border bg-card p-6 transition-colors hover:border-accent/50"
                  >
                    <Link href={`/tutorials/${tutorial.slug}`}>
                      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex-1">
                          <h2 className="text-xl font-semibold text-foreground group-hover:text-accent transition-colors">
                            {tutorial.title}
                          </h2>
                          <p className="mt-2 text-muted-foreground line-clamp-2">
                            {tutorial.description}
                          </p>
                          <div className="mt-4 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                            <span className="flex items-center gap-1.5">
                              <Calendar className="h-4 w-4" />
                              {new Date(tutorial.date).toLocaleDateString("en-US", {
                                year: "numeric",
                                month: "long",
                                day: "numeric",
                              })}
                            </span>
                            {tutorial.readingTime && (
                              <span className="flex items-center gap-1.5">
                                <Clock className="h-4 w-4" />
                                {tutorial.readingTime}
                              </span>
                            )}
                          </div>
                          {tutorial.tags && tutorial.tags.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {tutorial.tags.map((tag) => (
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
