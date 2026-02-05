import Link from "next/link"
import { ArrowRight, Calendar, Clock } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getAllPosts } from "@/lib/blog"

export const metadata = {
  title: "Blog | AI Engineering Lab",
  description: "Articles on AI engineering, MLOps, production systems, and building with data.",
}

export default async function BlogPage() {
  const posts = await getAllPosts()

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <section className="py-16 lg:py-24">
          <div className="mx-auto max-w-4xl px-6 lg:px-8">
            <div className="mb-12">
              <p className="text-sm font-medium uppercase tracking-widest text-accent">Blog</p>
              <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
                Insights & Updates
              </h1>
              <p className="mt-4 text-lg text-muted-foreground">
                Articles on AI engineering, production ML, and building real systems.
              </p>
            </div>

            {posts.length === 0 ? (
              <div className="rounded-lg border border-border bg-card p-12 text-center">
                <p className="text-lg text-muted-foreground">
                  No posts yet. Check back soon for articles on AI engineering, MLOps, and production systems.
                </p>
                <Link 
                  href="/#newsletter" 
                  className="mt-4 inline-flex items-center gap-2 text-accent hover:underline"
                >
                  Subscribe to get notified
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            ) : (
              <div className="space-y-8">
                {posts.map((post) => (
                  <article
                    key={post.slug}
                    className="group rounded-lg border border-border bg-card p-6 transition-colors hover:border-accent/50"
                  >
                    <Link href={`/blog/${post.slug}`}>
                      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex-1">
                          <h2 className="text-xl font-semibold text-foreground group-hover:text-accent transition-colors">
                            {post.title}
                          </h2>
                          <p className="mt-2 text-muted-foreground line-clamp-2">
                            {post.description}
                          </p>
                          <div className="mt-4 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                            <span className="flex items-center gap-1.5">
                              <Calendar className="h-4 w-4" />
                              {new Date(post.date).toLocaleDateString("en-US", {
                                year: "numeric",
                                month: "long",
                                day: "numeric",
                              })}
                            </span>
                            {post.readingTime && (
                              <span className="flex items-center gap-1.5">
                                <Clock className="h-4 w-4" />
                                {post.readingTime}
                              </span>
                            )}
                          </div>
                          {post.tags && post.tags.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {post.tags.map((tag) => (
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
