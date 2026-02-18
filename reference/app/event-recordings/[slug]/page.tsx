import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, Calendar, Video, Clock, ExternalLink, FileText, Code, Link as LinkIcon, Target, CheckCircle2, Wrench } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { getResourceBySlug, getAllResources } from "@/lib/resources"
import { ResourceVideoSection } from "@/components/resources/resource-video-section"

interface PageProps {
  params: Promise<{ slug: string }>
}

export async function generateStaticParams() {
  const resources = await getAllResources()
  return resources.map((resource) => ({ slug: resource.slug }))
}

export async function generateMetadata({ params }: PageProps) {
  const { slug } = await params
  const resource = await getResourceBySlug(slug)
  
  if (!resource) {
    return { title: "Resource Not Found" }
  }
  
  return {
    title: `${resource.title} | AI Shipping Labs`,
    description: resource.description,
  }
}

const getMaterialIcon = (type?: string) => {
  switch (type) {
    case "slides":
      return FileText
    case "code":
      return Code
    case "article":
      return LinkIcon
    default:
      return ExternalLink
  }
}

export default async function ResourcePage({ params }: PageProps) {
  const { slug } = await params
  const resource = await getResourceBySlug(slug)

  if (!resource) {
    notFound()
  }

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <article className="py-16 lg:py-24">
          <div className="mx-auto max-w-4xl px-6 lg:px-8">
            <Link
              href="/event-recordings"
              className="mb-8 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Event Recordings
            </Link>

            <header className="mb-12">
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
                <Video className="h-4 w-4" />
                Workshop Resource
              </div>
              <h1 className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl lg:text-5xl">
                {resource.title}
              </h1>
              
              {resource.description && (
                <p className="mt-4 text-xl text-muted-foreground">
                  {resource.description}
                </p>
              )}

              <div className="mt-6 flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Calendar className="h-4 w-4" />
                  {new Date(resource.date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </span>
                {resource.level && (
                  <span className="rounded-full bg-accent/20 px-3 py-1 text-xs font-medium text-accent">
                    {resource.level}
                  </span>
                )}
              </div>

              {resource.tags && resource.tags.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {resource.tags.map((tag) => (
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

            {/* Video Player and Timestamps */}
            <ResourceVideoSection
              googleEmbedUrl={resource.googleEmbedUrl}
              youtubeUrl={resource.youtubeUrl}
              title={resource.title}
              videoId={resource.slug}
              timestamps={resource.timestamps}
            />

            {/* Core Tools */}
            {resource.coreTools && resource.coreTools.length > 0 && (
              <div className="mb-12">
                <h2 className="mb-4 flex items-center gap-2 text-2xl font-semibold tracking-tight">
                  <Wrench className="h-6 w-6 text-accent" />
                  Core Tools
                </h2>
                <div className="flex flex-wrap gap-2">
                  {resource.coreTools.map((tool, index) => (
                    <span
                      key={index}
                      className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm text-foreground"
                    >
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Learning Objectives */}
            {resource.learningObjectives && resource.learningObjectives.length > 0 && (
              <div className="mb-12">
                <h2 className="mb-4 flex items-center gap-2 text-2xl font-semibold tracking-tight">
                  <Target className="h-6 w-6 text-accent" />
                  What You'll Learn
                </h2>
                <ul className="space-y-2">
                  {resource.learningObjectives.map((objective, index) => (
                    <li key={index} className="flex items-start gap-3">
                      <CheckCircle2 className="mt-0.5 h-5 w-5 flex-shrink-0 text-accent" />
                      <span className="text-muted-foreground">{objective}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Outcome */}
            {resource.outcome && (
              <div className="mb-12 rounded-lg border border-accent/20 bg-accent/5 p-6">
                <h2 className="mb-3 flex items-center gap-2 text-xl font-semibold tracking-tight">
                  <CheckCircle2 className="h-5 w-5 text-accent" />
                  Expected Outcome
                </h2>
                <p className="text-muted-foreground leading-relaxed">{resource.outcome}</p>
              </div>
            )}


            {/* Materials */}
            {resource.materials && resource.materials.length > 0 && (
              <div className="mb-12">
                <h2 className="mb-4 text-2xl font-semibold tracking-tight">Materials</h2>
                <div className="space-y-3">
                  {resource.materials.map((material, index) => {
                    const Icon = getMaterialIcon(material.type)
                    return (
                      <a
                        key={index}
                        href={material.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="group flex items-center gap-4 rounded-lg border border-border bg-card p-4 transition-colors hover:border-accent/50 hover:bg-card/80"
                      >
                        <div className="flex-shrink-0 rounded-lg bg-accent/20 p-2">
                          <Icon className="h-5 w-5 text-accent" />
                        </div>
                        <div className="flex-1">
                          <h3 className="font-semibold text-foreground group-hover:text-accent transition-colors">
                            {material.title}
                          </h3>
                          {material.type && (
                            <p className="mt-1 text-xs uppercase tracking-wider text-muted-foreground">
                              {material.type}
                            </p>
                          )}
                        </div>
                        <ExternalLink className="h-5 w-5 text-muted-foreground group-hover:text-accent transition-colors" />
                      </a>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </article>
      </main>
      <Footer />
    </>
  )
}
