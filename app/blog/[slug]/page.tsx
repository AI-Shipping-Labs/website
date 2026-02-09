import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, Calendar, Clock } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getPostBySlug, getAllPosts } from "@/lib/blog"

interface PageProps {
  params: Promise<{ slug: string }>
}

export async function generateStaticParams() {
  const posts = await getAllPosts()
  return posts.map((post) => ({ slug: post.slug }))
}

export async function generateMetadata({ params }: PageProps) {
  const { slug } = await params
  const post = await getPostBySlug(slug)
  
  if (!post) {
    return { title: "Post Not Found" }
  }
  
  return {
    title: `${post.title} | AI Engineering Lab`,
    description: post.description,
  }
}

export default async function BlogPostPage({ params }: PageProps) {
  const { slug } = await params
  const post = await getPostBySlug(slug)

  if (!post) {
    notFound()
  }

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <article className="py-16 lg:py-24">
          <div className="mx-auto max-w-3xl px-6 lg:px-8">
            <Link
              href="/blog"
              className="mb-8 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Blog
            </Link>

            <header className="mb-12">
              <h1 className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl lg:text-5xl">
                {post.title}
              </h1>
              
              {post.description && (
                <p className="mt-4 text-xl text-muted-foreground">
                  {post.description}
                </p>
              )}

              <div className="mt-6 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
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
                <div className="mt-4 flex flex-wrap gap-2">
                  {post.tags.map((tag) => (
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
                prose-img:rounded-lg prose-img:border prose-img:border-border prose-img:my-0 prose-img:w-full
                prose-figcaption:text-center prose-figcaption:text-sm prose-figcaption:italic prose-figcaption:text-muted-foreground/80 prose-figcaption:mt-4 prose-figcaption:leading-relaxed
                [&_figure]:my-12 [&_figure]:border [&_figure]:border-border/50 [&_figure]:rounded-lg [&_figure]:p-4 [&_figure]:bg-card/30"
              dangerouslySetInnerHTML={{ __html: post.contentHtml }}
            />
          </div>
        </article>
      </main>
      <Footer />
    </>
  )
}
