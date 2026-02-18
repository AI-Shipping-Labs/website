"use client"

import { Mail, ArrowRight } from "lucide-react"
import { Button } from "@/components/ui/button"

export function Newsletter() {
  return (
    <section id="newsletter" className="border-t border-border bg-card py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="mx-auto max-w-2xl">
          <div className="rounded-2xl border border-border bg-background p-8 sm:p-10">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
              <Mail className="h-4 w-4" />
              Free Newsletter
            </div>
            <h2 className="text-balance text-2xl font-semibold tracking-tight sm:text-3xl">
              Ready to turn your AI ideas into real projects?
            </h2>
            <p className="mt-4 text-pretty text-muted-foreground leading-relaxed">
              Subscribe to the free newsletter and get notified when the community opens. 
              Join action-oriented builders who are shipping practical AI products.
            </p>
            <div className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
              <Button asChild size="lg" className="bg-accent text-accent-foreground hover:bg-accent/90 shrink-0">
                <a
                  href="https://alexeyondata.substack.com/subscribe"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2"
                >
                  Subscribe
                  <ArrowRight className="h-4 w-4" />
                </a>
              </Button>
              <p className="text-sm text-muted-foreground">
                No spam. Unsubscribe anytime.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
