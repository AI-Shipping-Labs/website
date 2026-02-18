"use client"

import { useState } from "react"
import Link from "next/link"
import { Menu, X, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function Header() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  return (
    <header className="fixed top-0 left-0 right-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
        <Link href="/" className="flex items-center gap-2">
          <div className="h-8 w-8 rounded bg-accent" />
          <span className="text-lg font-semibold tracking-tight">AI Shipping Labs</span>
        </Link>

        <div className="hidden md:flex md:items-center md:gap-8">
          <Link href="/about" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            About
          </Link>
          <Link href="/activities" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Activities
          </Link>
          <Link href="/#tiers" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
            Membership
          </Link>
          <DropdownMenu>
            <DropdownMenuTrigger className="text-sm text-muted-foreground transition-colors hover:text-foreground flex items-center gap-1 outline-none">
              Resources
              <ChevronDown className="h-4 w-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-48">
              <DropdownMenuItem asChild>
                <Link href="/blog" className="cursor-pointer">
                  Blog
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/projects" className="cursor-pointer">
                  Project Ideas
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/event-recordings" className="cursor-pointer">
                  Event Recordings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/collection" className="cursor-pointer">
                  Curated Links
                </Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
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
              About
            </Link>
            <Link
              href="/activities"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Activities
            </Link>
            <Link
              href="/#tiers"
              className="block py-2 text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => setMobileMenuOpen(false)}
            >
              Membership
            </Link>
            <div className="py-2">
              <p className="text-sm font-medium text-foreground mb-2">Resources</p>
              <div className="pl-4 space-y-1">
                <Link
                  href="/blog"
                  className="block py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  Blog
                </Link>
                <Link
                  href="/projects"
                  className="block py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  Project Ideas
                </Link>
                <Link
                  href="/event-recordings"
                  className="block py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  Event Recordings
                </Link>
                <Link
                  href="/collection"
                  className="block py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  Curated Links
                </Link>
              </div>
            </div>
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
