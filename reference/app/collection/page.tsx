import { FolderOpen } from "lucide-react"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { CollectionGrid } from "@/components/landing/collection-grid"

export const metadata = {
  title: "Curated Links | AI Shipping Labs",
  description:
    "Curated GitHub tools, model hubs, courses, and learning resources. Dev tools, local LLMs, and courses.",
}

export default function CollectionPage() {
  return (
    <>
      <Header />
      <main className="min-h-screen pt-24">
        <section className="py-16 lg:py-24">
          <div className="mx-auto max-w-7xl px-6 lg:px-8">
            <div className="mb-12">
              <p className="inline-flex items-center gap-2 text-sm font-medium uppercase tracking-widest text-accent">
                <FolderOpen className="h-4 w-4" />
                Curated Links
              </p>
              <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
                Tools, Models & Courses
              </h1>
              <p className="mt-4 max-w-2xl text-lg text-muted-foreground">
                Curated links to GitHub repos, model hubs, and learning resources. 
                Filter by category or browse all.
              </p>
            </div>

            <CollectionGrid />
          </div>
        </section>
      </main>
      <Footer />
    </>
  )
}
