"use client"

import { useState } from "react"
import { Check, X, Star } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { getPaymentLink, type StripeTier } from "@/lib/stripe-links"

const tiers = [
  {
    name: "Basic",
    stripeKey: "basic" as StripeTier,
    tagline: "Content only",
    description: "Access curated educational content, tutorials, and research. Perfect for self-directed builders who learn at their own pace.",
    priceMonthly: 20,
    priceAnnual: 200,
    hook: "Educational content without community access.",
    features: [
      { text: "Full access to exclusive Substack content", included: true },
      { text: "Hands-on tutorials with code examples you can implement", included: true },
      { text: "Curated breakdowns of new AI tools and workflows", included: true },
      { text: "Behind-the-scenes access to ongoing research and experiments", included: true },
      { text: "Curated collection of valuable social posts you might have missed", included: true }
    ],
    positioning: "Best for independent builders who prefer self-paced learning. Upgrade to Main for structure, accountability, and community support.",
    highlighted: false,
  },
  {
    name: "Main",
    stripeKey: "main" as StripeTier,
    tagline: "Live learning + community",
    description: "Everything in Basic, plus the structure, accountability, and peer support to ship your AI projects consistently.",
    priceMonthly: 50,
    priceAnnual: 500,
    hook: "Build with the community and get the accountability and direction you need to make progress.",
    features: [
      { text: "Everything in Basic", included: true },
      { text: "Closed community access to connect and interact with practitioners", included: true },
      { text: "Collaborative problem-solving and mentorship for implementation challenges", included: true },
      { text: "Interactive group coding sessions led by a host", included: true },
      { text: "Guided project-based learning with curated resources", included: true },
      { text: "Community hackathons", included: true },
      { text: "Career advancement discussions and feedback", included: true },
      { text: "Personal brand development guidance and content", included: true },
      { text: "Developer productivity tips and workflows", included: true },
      { text: "Propose and vote on future topics", included: true },
    ],
    positioning: "Best for builders who need structure and accountability to turn project ideas into reality alongside motivated peers.",
    highlighted: true,
  },
  {
    name: "Premium",
    stripeKey: "premium" as StripeTier,
    tagline: "Courses + personalized feedback",
    description: "Everything in Main, plus structured learning paths through mini-courses and personalized career guidance to accelerate your growth.",
    priceMonthly: 100,
    priceAnnual: 1000,
    hook: "Accelerate your growth with structured courses and personalized feedback.",
    features: [
      { text: "Everything in Main", included: true },
      { text: "Access to all mini-courses on specialized topics", included: true },
      { text: "Collection regularly updated with new courses", included: true },
      { text: "Upcoming: Python for Data and AI Engineering", included: true },
      { text: "Propose and vote on mini-course topics", included: true },
      { text: "Resume, LinkedIn, and GitHub teardowns", included: true },
    ],
    positioning: "Best for builders seeking structured learning paths to complement hands-on projects, plus personalized career guidance.",
    highlighted: false,
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
            Each tier is designed for a different type of builder. More investment means more structure, accountability, 
            and support to help you ship your AI projects consistently.
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
                "relative flex flex-col rounded-xl border p-8 transition-all",
                tier.highlighted
                  ? "border-accent bg-background shadow-xl shadow-accent/10 ring-2 ring-accent/20 lg:scale-105"
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
                {tier.features.map((feature, index) => (
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
                      ? "bg-accent text-accent-foreground hover:bg-accent/90 shadow-lg shadow-accent/20"
                      : "bg-secondary text-foreground hover:bg-secondary/80"
                  )}
                >
                  <a href={getPaymentLink(tier.stripeKey, annual)} target="_blank" rel="noopener noreferrer">
                    {tier.highlighted ? "Get Started" : `Choose ${tier.name}`}
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
