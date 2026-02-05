"use client"

import { useState } from "react"
import { Check, X, Star } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const tiers = [
  {
    name: "Supporter",
    tagline: "Read and watch",
    description: "Support the newsletter and get access to Alexey Grigorev's written thinking and recorded content.",
    priceMonthly: 10,
    priceAnnual: 100,
    hook: "Two coffees a month for filtered signal.",
    features: [
      { text: "Support the newsletter", included: true },
      { text: "Paid written materials on Substack", included: true },
      { text: "Opinionated briefs on tools and practices", included: true },
      { text: "Tutorials and hands-on materials", included: true },
      { text: "YouTube recordings of live streams", included: true },
      { text: "Slack community access", included: false },
      { text: "Live participation", included: false },
      { text: "Topic influence and voting", included: false },
      { text: "Direct interaction with Alexey Grigorev", included: false },
    ],
    positioning: "For readers who want signal, judgment, and context without community participation.",
    highlighted: false,
  },
  {
    name: "Community Member",
    tagline: "Discuss, vote, and build together",
    description: "Participate in discussions, influence topics, and join live problem-solving.",
    priceMonthly: 35,
    priceAnnual: 350,
    hook: "Comparable to professional communities, not courses.",
    features: [
      { text: "Everything in Supporter tier", included: true },
      { text: "Closed Slack community access", included: true },
      { text: "Community activities (Project of the Week)", included: true },
      { text: "Propose and vote on topics", included: true },
      { text: "Join live streams with Alexey Grigorev", included: true },
      { text: "Structured Q&A and live questions", included: true },
      { text: "Early access to paid materials", included: true },
      { text: "Guaranteed 1-on-1 feedback", included: false },
      { text: "Personalized review of work", included: false },
    ],
    positioning: "For practitioners who want to think along, contribute, and learn through shared reasoning.",
    highlighted: true,
  },
  {
    name: "Inner Circle",
    tagline: "Get feedback and direct access",
    description: "High-trust, high-touch access focused on calibration: feedback on positioning, decisions, and work-in-progress.",
    priceMonthly: 120,
    priceAnnual: 1200,
    hook: "Capped membership. Quality over quantity.",
    features: [
      { text: "Everything in Community tier", included: true },
      { text: "Exclusive calibration sessions", included: true },
      { text: "Resume, LinkedIn, GitHub teardowns", included: true },
      { text: "Eligibility for 1-on-1 conversations", included: true },
      { text: "Small-group exploratory sessions", included: true },
      { text: "Priority in topic selection", included: true },
      { text: "Access to all session outcomes", included: true },
      { text: "Guaranteed monthly 1-on-1", included: false },
      { text: "Consulting or coaching services", included: false },
    ],
    positioning: "For members who are building and want their work, positioning, and decisions calibrated—not more content.",
    highlighted: false,
    capped: true,
  },
]

export function Pricing() {
  const [annual, setAnnual] = useState(true)

  return (
    <section id="tiers" className="border-t border-border bg-card py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-medium uppercase tracking-widest text-accent">Membership</p>
          <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
            Choose your level of engagement
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground">
            Each tier is designed for a different type of practitioner. More money doesn't mean more content—it means more access and interaction.
          </p>
          
          <div className="mt-8 flex items-center justify-center gap-4">
            <span className={cn("text-sm", !annual && "text-foreground", annual && "text-muted-foreground")}>
              Monthly
            </span>
            <button
              type="button"
              onClick={() => setAnnual(!annual)}
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
                annual ? "bg-accent" : "bg-secondary"
              )}
              aria-label="Toggle annual pricing"
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-foreground shadow ring-0 transition-transform",
                  annual ? "translate-x-5" : "translate-x-0"
                )}
              />
            </button>
            <span className={cn("text-sm", annual && "text-foreground", !annual && "text-muted-foreground")}>
              Annual <span className="text-accent">(Save ~17%)</span>
            </span>
          </div>
        </div>

        <div className="mx-auto mt-16 grid max-w-7xl gap-6 lg:grid-cols-3">
          {tiers.map((tier) => (
            <div
              key={tier.name}
              className={cn(
                "relative flex flex-col rounded-xl border p-8",
                tier.highlighted
                  ? "border-accent bg-background"
                  : "border-border bg-background"
              )}
            >
              {tier.highlighted && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                  <span className="inline-flex items-center gap-1 rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-foreground">
                    <Star className="h-3 w-3" />
                    Most Popular
                  </span>
                </div>
              )}
              
              {tier.capped && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                  <span className="inline-flex items-center gap-1 rounded-full bg-secondary px-3 py-1 text-xs font-medium text-foreground">
                    Limited Seats
                  </span>
                </div>
              )}

              <div className="mb-6">
                <h3 className="text-lg font-semibold text-foreground">{tier.name}</h3>
                <p className="mt-1 text-sm text-accent">{tier.tagline}</p>
              </div>

              <div className="mb-6">
                <div className="flex items-baseline">
                  <span className="text-4xl font-semibold text-foreground">
                    €{annual ? tier.priceAnnual : tier.priceMonthly}
                  </span>
                  <span className="ml-2 text-muted-foreground">
                    /{annual ? "year" : "month"}
                  </span>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{tier.hook}</p>
              </div>

              <p className="mb-6 text-sm text-muted-foreground">{tier.description}</p>

              <ul className="mb-8 flex-1 space-y-3">
                {tier.features.map((feature) => (
                  <li key={feature.text} className="flex items-start gap-3">
                    {feature.included ? (
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                    ) : (
                      <X className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/50" />
                    )}
                    <span
                      className={cn(
                        "text-sm",
                        feature.included ? "text-foreground" : "text-muted-foreground/50"
                      )}
                    >
                      {feature.text}
                    </span>
                  </li>
                ))}
              </ul>

              <div className="mt-auto space-y-4">
                <p className="text-xs text-muted-foreground">{tier.positioning}</p>
                <Button
                  asChild
                  className={cn(
                    "w-full",
                    tier.highlighted
                      ? "bg-accent text-accent-foreground hover:bg-accent/90"
                      : "bg-secondary text-foreground hover:bg-secondary/80"
                  )}
                >
                  <a href="/#newsletter">
                    Subscribe for updates
                  </a>
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
