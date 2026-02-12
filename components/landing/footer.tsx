import Link from "next/link"
import { Button } from "@/components/ui/button"

export function Footer() {
  return (
    <footer className="border-t border-border bg-card">
      <div className="mx-auto max-w-7xl px-6 py-16 lg:px-8 lg:py-24">
        <div className="mx-auto max-w-2xl">
          <div className="rounded-2xl border border-border bg-background p-6 sm:p-8 text-center">
            <h2 className="text-balance text-xl font-semibold tracking-tight sm:text-2xl">
              Want to know when we launch?
            </h2>
            <p className="mt-3 text-sm text-muted-foreground">
              Subscribe to the free newsletter and get the first ping when the community opens.
            </p>
            <div className="mt-6 flex justify-center">
              <Button asChild className="bg-accent text-accent-foreground hover:bg-accent/90">
                <a
                  href="https://alexeyondata.substack.com"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Subscribe to newsletter
                </a>
              </Button>
            </div>
          </div>
        </div>

        <div className="mt-16 grid gap-8 border-t border-border pt-8 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <div className="flex items-center gap-2">
              <div className="h-6 w-6 rounded bg-accent" />
              <span className="font-semibold">AI Shipping Labs</span>
            </div>
            <p className="mt-4 text-sm text-muted-foreground">
              A technical community for AI, data, and engineering practitioners.
            </p>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-foreground">Community</h3>
            <ul className="mt-4 space-y-3">
              <li>
                <Link href="/about" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  About Alexey Grigorev
                </Link>
              </li>
              <li>
                <Link href="/topics" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  Topics
                </Link>
              </li>
              <li>
                <Link href="/#tiers" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  Membership Tiers
                </Link>
              </li>
              <li>
                <Link href="/#faq" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  FAQ
                </Link>
              </li>
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-foreground">Connect</h3>
            <ul className="mt-4 space-y-3">
              <li>
                <a href="https://alexeyondata.substack.com" target="_blank" rel="noopener noreferrer" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  Substack
                </a>
              </li>
              <li>
                <a href="https://youtube.com/@datatalksclub" target="_blank" rel="noopener noreferrer" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  YouTube
                </a>
              </li>
              <li>
                <a href="https://linkedin.com/in/agrigorev" target="_blank" rel="noopener noreferrer" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  LinkedIn
                </a>
              </li>
              <li>
                <a href="https://datatalks.club" target="_blank" rel="noopener noreferrer" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  DataTalks.Club
                </a>
              </li>
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-foreground">Legal</h3>
            <ul className="mt-4 space-y-3">
              <li>
                <Link href="#" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  Privacy Policy
                </Link>
              </li>
              <li>
                <Link href="#" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                  Terms of Service
                </Link>
              </li>
            </ul>
          </div>
        </div>

        <div className="mt-8 border-t border-border pt-8 text-center">
          <p className="text-sm text-muted-foreground">
            © {new Date().getFullYear()} AI Shipping Labs by Alexey Grigorev. All rights reserved.
          </p>
        </div>
      </div>
    </footer>
  )
}
