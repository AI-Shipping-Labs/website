import Link from "next/link"
import { ArrowRight, Calendar, Clock, PenLine } from "lucide-react"

import { getAllPosts } from "@/lib/blog"

export async function BlogSection() {
  const posts = await getAllPosts()
  const latestPosts = posts.slice(0, 3)

  return (
    <section id="blog" className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
              <PenLine className="h-4 w-4" />
              From the blog
            </p>
            <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
              Publish and share our thinking
            </h2>
            <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
              Long-form notes, walkthroughs, and experiments in markdown. Stay close to how we build and reason.
            </p>
          </div>
          <Link
            href="/blog"
            className="inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
          >
            View all posts
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        {latestPosts.length === 0 ? (
          <div className="mt-12 rounded-lg border border-border bg-card p-8 text-center">
            <p className="text-muted-foreground">
              We&apos;re drafting the first articles now. Sign up below to get them in your inbox the moment they drop.
            </p>
            <div className="mt-6 flex justify-center">
              <div className="w-full max-w-md overflow-hidden rounded-lg border border-border bg-background">
                <iframe
                  src="https://alexeyondata.substack.com/embed"
                  width="100%"
                  height="150"
                  className="block"
                  style={{ border: 0 }}
                  frameBorder="0"
                  scrolling="no"
                  title="Newsletter sign up"
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {latestPosts.map((post) => (
              <article
                key={post.slug}
                className="group flex flex-col rounded-xl border border-border bg-card p-6 transition-colors hover:border-accent/50"
              >
                <div className="flex-1">
                  <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Markdown</p>
                  <h3 className="mt-3 text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
                    {post.title}
                  </h3>
                  <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">
                    {post.description}
                  </p>

                  <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <Calendar className="h-4 w-4" />
                      {new Date(post.date).toLocaleDateString("en-US", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                    {post.readingTime && (
                      <span className="inline-flex items-center gap-1.5">
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

                <Link
                  href={`/blog/${post.slug}`}
                  className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
                >
                  Read article
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
