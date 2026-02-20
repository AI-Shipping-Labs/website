"use client"

import { useState } from "react"
import { ExternalLink, Wrench, Cpu, GraduationCap, FolderOpen } from "lucide-react"
import {
  COLLECTION_ITEMS,
  COLLECTION_CATEGORIES,
  getAllCollectionCategories,
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

export function CollectionGrid() {
  const [category, setCategory] = useState<CollectionCategory | "all">("all")
  const categories = getAllCollectionCategories()
  const items =
    category === "all"
      ? COLLECTION_ITEMS
      : COLLECTION_ITEMS.filter((item) => item.category === category)

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setCategory("all")}
          className={`rounded-full px-4 py-2 text-sm font-medium transition-colors ${
            category === "all"
              ? "bg-accent text-accent-foreground"
              : "bg-secondary text-muted-foreground hover:bg-secondary/80 hover:text-foreground"
          }`}
        >
          All
        </button>
        {categories.map((cat) => (
          <button
            key={cat}
            type="button"
            onClick={() => setCategory(cat)}
            className={`rounded-full px-4 py-2 text-sm font-medium transition-colors ${
              category === cat
                ? "bg-accent text-accent-foreground"
                : "bg-secondary text-muted-foreground hover:bg-secondary/80 hover:text-foreground"
            }`}
          >
            {COLLECTION_CATEGORIES[cat].label}
          </button>
        ))}
      </div>

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item) => (
          <CollectionCard key={item.id} item={item} />
        ))}
      </div>

      {items.length === 0 && (
        <p className="py-12 text-center text-muted-foreground">No items in this category yet.</p>
      )}
    </div>
  )
}
