import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, Calendar, Clock, BookOpen } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getTutorialBySlug, getAllTutorials } from "@/lib/tutorials"

interface PageProps {
  params: Promise<{ slug: string }>
}

export async function generateStaticParams() {
  const tutorials = await getAllTutorials()
  // Static export requires at least one path; use placeholder when empty
  if (tutorials.length === 0) return [{ slug: "_" }]
  return tutorials.map((tutorial) => ({ slug: tutorial.slug }))
}

export async function generateMetadata({ params }: PageProps) {
  const { slug } = await params
  const tutorial = await getTutorialBySlug(slug)
  
  if (!tutorial) {
    return { title: "Tutorial Not Found" }
  }
  
  return {
    title: `${tutorial.title} | AI Shipping Labs`,
    description: tutorial.description,
  }
}

export default async function TutorialPage({ params }: PageProps) {
  const { slug } = await params
  const tutorial = await getTutorialBySlug(slug)

  if (!tutorial) {
    if (slug === "_") {
      return (
        <>
          <Header />
          <main className="min-h-screen pt-24">
            <div className="mx-auto max-w-3xl px-6 py-24 lg:px-8">
              <Link
                href="/tutorials"
                className="mb-8 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                <ArrowLeft className="h-4 w-4" />
                Back to Tutorials
              </Link>
              <p className="text-muted-foreground">Tutorials coming soon.</p>
            </div>
          </main>
          <Footer />
        </>
      )
    }
    notFound()
  }

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <article className="py-16 lg:py-24">
          <div className="mx-auto max-w-3xl px-6 lg:px-8">
            <Link
              href="/tutorials"
              className="mb-8 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Tutorials
            </Link>

            <header className="mb-12">
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
                <BookOpen className="h-4 w-4" />
                Tutorial
              </div>
              <h1 className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl lg:text-5xl">
                {tutorial.title}
              </h1>
              
              {tutorial.description && (
                <p className="mt-4 text-xl text-muted-foreground">
                  {tutorial.description}
                </p>
              )}

              <div className="mt-6 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
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
                <div className="mt-4 flex flex-wrap gap-2">
                  {tutorial.tags.map((tag) => (
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
              dangerouslySetInnerHTML={{ __html: tutorial.contentHtml }}
            />
          </div>
        </article>
      </main>
      <Footer />
    </>
  )
}
