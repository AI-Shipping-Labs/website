"use client"

import { useState } from "react"
import Link from "next/link"
import { Menu, X } from "lucide-react"
import { Button } from "@/components/ui/button"

export function Header() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  return (
    <header className="fixed top-0 left-0 right-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded bg-accent" />
          <span className="text-lg font-semibold tracking-tight">AI Shipping Labs</span>
        </div>

        <div className="hidden md:flex md:items-center md:gap-8">
          <Link href="/about" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            About Alexey
          </Link>
          <Link href="/topics" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Topics
          </Link>
          <Link href="/#tiers" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Membership
          </Link>
          <Link href="/blog" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Blog
          </Link>
          <Link href="/tutorials" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Tutorials
          </Link>
          <Link href="/projects" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Project Ideas
          </Link>
          <Link href="/resources" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Resources
          </Link>
          <Link href="/collection" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Collection
          </Link>
          <Link href="/#faq" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            FAQ
          </Link>
        </div>

        <div className="hidden md:block">
          <Button asChild variant="outline" className="border-border text-foreground hover:bg-secondary bg-transparent">
            <Link href="/#newsletter">
              Subscribe for updates
            </Link>
          </Button>
        </div>

        <button
          type="button"
          className="md:hidden"
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          aria-label="Toggle menu"
        >
          {mobileMenuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </nav>

      {mobileMenuOpen && (
        <div className="border-t border-border bg-background md:hidden">
          <div className="space-y-1 px-6 py-4">
            <Link
              href="/about"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              About Alexey
            </Link>
            <Link
              href="/topics"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Topics
            </Link>
            <Link
              href="/#tiers"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Membership
            </Link>
            <Link
              href="/blog"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Blog
            </Link>
            <Link
              href="/tutorials"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Tutorials
            </Link>
            <Link
              href="/projects"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Project Ideas
            </Link>
            <Link
              href="/resources"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Resources
            </Link>
            <Link
              href="/collection"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Collection
            </Link>
            <Link
              href="/#faq"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              FAQ
            </Link>
            <Button asChild variant="outline" className="mt-4 w-full border-border text-foreground hover:bg-secondary bg-transparent">
              <Link href="/#newsletter">
                Subscribe for updates
              </Link>
            </Button>
          </div>
        </div>
      )}
    </header>
  )
}
