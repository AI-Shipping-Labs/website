import Link from "next/link"
import { ArrowRight, ExternalLink, Wrench, Cpu, GraduationCap, FolderOpen } from "lucide-react"
import {
  COLLECTION_ITEMS,
  COLLECTION_CATEGORIES,
  type CollectionCategory,
  type CollectionItem,
} from "@/lib/collection"

const CATEGORY_ICONS: Record<CollectionCategory, React.ComponentType<{ className?: string }>> = {
  tools: Wrench,
  models: Cpu,
  courses: GraduationCap,
  other: FolderOpen,
}

function CollectionCard({ item }: { item: CollectionItem }) {
  const Icon = CATEGORY_ICONS[item.category]
  const isExternal = item.url.startsWith("http")

  return (
    <a
      href={item.url}
      target={isExternal ? "_blank" : undefined}
      rel={isExternal ? "noopener noreferrer" : undefined}
      className="group flex flex-col rounded-xl border border-border bg-card p-6 transition-colors hover:border-accent/50"
    >
      <div className="flex flex-1 flex-col">
        <div className="mb-2 flex items-start justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            <Icon className="h-3.5 w-3.5" />
            {COLLECTION_CATEGORIES[item.category].label}
          </span>
          {isExternal && (
            <ExternalLink className="h-4 w-4 shrink-0 text-muted-foreground transition-colors group-hover:text-accent" />
          )}
        </div>
        <h3 className="text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
          {item.title}
        </h3>
        <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">{item.description}</p>
        {item.source && (
          <p className="mt-3 text-xs text-muted-foreground">{item.source}</p>
        )}
      </div>
    </a>
  )
}

export function CollectionSection() {
  const previewItems = COLLECTION_ITEMS.slice(0, 6)

  return (
    <section id="collection" className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
              <FolderOpen className="h-4 w-4" />
              Curated Links
            </p>
            <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
              Tools, Models & Courses
            </h2>
            <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
              Curated GitHub repos, model hubs, and learning resources. 
              Dev tools, local LLMs, and courses to level up.
            </p>
          </div>
          <Link
            href="/collection"
            className="inline-flex items-center gap-2 text-sm font-medium text-accent transition-colors hover:text-accent/80"
          >
            View all curated links
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {previewItems.map((item) => (
            <CollectionCard key={item.id} item={item} />
          ))}
        </div>
      </div>
    </section>
  )
}
