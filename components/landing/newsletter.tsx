"use client"

import { Mail } from "lucide-react"

export function Newsletter() {
  return (
    <section id="newsletter" className="border-t border-border bg-card py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-sm text-accent">
            <Mail className="h-4 w-4" />
            Free Newsletter
          </div>
          
          <h2 className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
            Stay in the loop
          </h2>
          
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground">
            The community is launching soon. Subscribe to Alexey's free newsletter to get updates, 
            early access announcements, and quality content on AI engineering, MLOps, and production systems.
          </p>
          
          <div className="mt-10 flex justify-center">
            <iframe 
              src="https://alexeyondata.substack.com/embed" 
              width="480" 
              height="150" 
              className="max-w-full rounded-lg border border-border"
              style={{ background: 'transparent' }}
              title="Alexey on Data Newsletter Signup"
            />
          </div>
          
          <p className="mt-8 text-sm text-muted-foreground">
            Join thousands of practitioners already reading. No spam, unsubscribe anytime.
          </p>
        </div>
      </div>
    </section>
  )
}
