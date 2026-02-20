"use client"

import React from "react"

import { useState } from "react"
import { 
  Video, 
  MessageCircleQuestion, 
  FileEdit, 
  Eye, 
  BookOpen, 
  Percent, 
  FolderKanban, 
  Trophy,
  Check,
  Users,
  Star,
  Briefcase
} from "lucide-react"
import { cn } from "@/lib/utils"

type TierKey = "basic" | "main" | "premium"

interface Activity {
  icon: React.ElementType
  title: string
  description: string
  tiers: TierKey[]
}

const activities: Activity[] = [
  {
    icon: BookOpen,
    title: "Exclusive Substack Content",
    description: "Full access to premium paywalled articles with practical AI insights, hands-on tutorials with code examples you can implement, and curated breakdowns of new AI tools and workflows to accelerate your projects.",
    tiers: ["basic", "main", "premium"],
  },
  {
    icon: Eye,
    title: "Behind-the-Scenes Research",
    description: "Get exclusive access to ongoing research and experiments. See work-in-progress findings and early-stage ideas not available publicly.",
    tiers: ["basic", "main", "premium"],
  },
  {
    icon: FileEdit,
    title: "Curated Social Content Collection",
    description: "Never miss valuable educational posts again. Get a curated collection of evergreen social media content you can reference anytime.",
    tiers: ["basic", "main", "premium"],
  },
  {
    icon: Users,
    title: "Closed Community Access",
    description: "Connect with action-oriented builders who are shipping practical AI products. Network with motivated peers, collaborate on projects, and learn from practitioners who convert ideas into tangible contributions.",
    tiers: ["main", "premium"],
  },
  {
    icon: MessageCircleQuestion,
    title: "Collaborative Problem-Solving & Mentorship",
    description: "Get help with implementation challenges and complex issues. Learn from practitioners at various career stages and receive guidance on technical problems you're facing.",
    tiers: ["main", "premium"],
  },
  {
    icon: Video,
    title: "Interactive Group Coding Sessions",
    description: "Join sessions where community members and hosts code live, working through real problems. Watch, participate, and engage with comments as you learn.",
    tiers: ["main", "premium"],
  },
  {
    icon: FolderKanban,
    title: "Guided Project-Based Learning",
    description: "Get the structure and direction you need to make consistent progress. Follow curated project frameworks, share your progress with the community, and build practical AI products with clear milestones.",
    tiers: ["main", "premium"],
  },
  {
    icon: Trophy,
    title: "Community Hackathons",
    description: "Turn ideas into shipped projects through focused hackathons. Get gentle external pressure and accountability to build, share your work, and learn from other builders' approaches. Many members emerge from hackathons as active contributors.",
    tiers: ["main", "premium"],
  },
  {
    icon: Briefcase,
    title: "Career Advancement Discussions",
    description: "Discuss your career questions and get feedback from experienced practitioners in the community. Share experiences, get advice on job searches, interviews, and career growth.",
    tiers: ["main", "premium"],
  },
  {
    icon: Star,
    title: "Personal Brand Development",
    description: "Share your project results publicly and strengthen your professional presence. Get guidance on showcasing your work, building in public, and demonstrating real-world impact. Especially valuable for career transitioners and early career professionals.",
    tiers: ["main", "premium"],
  },
  {
    icon: Percent,
    title: "Developer Productivity Tips & Workflows",
    description: "Get tips, workflows, and best practices to boost your productivity as a developer. Learn techniques to work more efficiently and effectively.",
    tiers: ["main", "premium"],
  },
  {
    icon: FileEdit,
    title: "Propose and Vote on Topics",
    description: "Have a voice in the community's direction. Propose ideas and vote on future topics for content, workshops, and sessions.",
    tiers: ["main", "premium"],
  },
  {
    icon: BookOpen,
    title: "Mini-Courses on Specialized Topics",
    description: "Access all mini-courses covering specialized topics like Python for Data & AI Engineering, and more. The collection is regularly updated with new courses.",
    tiers: ["premium"],
  },
  {
    icon: FileEdit,
    title: "Vote on Course Topics",
    description: "Have a say in what gets taught next. Propose ideas and vote on upcoming mini-course topics to shape the curriculum.",
    tiers: ["premium"],
  },
  {
    icon: Users,
    title: "Profile Teardowns",
    description: "Get detailed feedback on your resume, LinkedIn, and GitHub profiles. Understand what works, what doesn't, and how to improve your professional presence.",
    tiers: ["premium"],
  },
]

const tierConfig = {
  basic: {
    name: "Basic",
    color: "border-muted-foreground/30",
    bgActive: "bg-muted-foreground/20",
    textActive: "text-muted-foreground",
  },
  main: {
    name: "Main",
    color: "border-accent",
    bgActive: "bg-accent",
    textActive: "text-accent-foreground",
  },
  premium: {
    name: "Premium",
    color: "border-foreground",
    bgActive: "bg-foreground",
    textActive: "text-background",
  },
}

export function Activities() {
  const [selectedTier, setSelectedTier] = useState<TierKey | "all">("all")

  const filteredActivities = selectedTier === "all" 
    ? activities 
    : activities.filter(a => a.tiers.includes(selectedTier))

  return (
    <section className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-medium uppercase tracking-widest text-accent">What You Get</p>
          <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
            Activities and access by tier
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground">
            Each tier gives you more structure, accountability, and support to ship your AI projects. 
            From self-paced content to guided projects and peer collaboration. Filter by tier to see what's included.
          </p>
        </div>

        {/* Tier Filter */}
        <div className="mx-auto mt-10 flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => setSelectedTier("all")}
            className={cn(
              "rounded-full px-4 py-2 text-sm font-medium transition-colors",
              selectedTier === "all"
                ? "bg-accent text-accent-foreground"
                : "bg-secondary text-muted-foreground hover:text-foreground"
            )}
          >
            All Activities
          </button>
          {(Object.keys(tierConfig) as TierKey[]).map((tier) => (
            <button
              key={tier}
              type="button"
              onClick={() => setSelectedTier(tier)}
              className={cn(
                "rounded-full px-4 py-2 text-sm font-medium transition-colors",
                selectedTier === tier
                  ? "bg-accent text-accent-foreground"
                  : "bg-secondary text-muted-foreground hover:text-foreground"
              )}
            >
              {tierConfig[tier].name}
            </button>
          ))}
        </div>

        {/* Activities Grid */}
        <div className="mx-auto mt-12 grid max-w-6xl gap-6 md:grid-cols-2">
          {filteredActivities.map((activity) => (
            <div
              key={activity.title}
              className="group rounded-xl border border-border bg-card p-6 transition-colors hover:border-accent/50"
            >
              <div className="flex items-start gap-4">
                <div className="shrink-0 rounded-lg bg-secondary p-3">
                  <activity.icon className="h-5 w-5 text-accent" />
                </div>
                <div className="flex-1">
                  <h3 className="font-semibold text-foreground">{activity.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {activity.description}
                  </p>
                  
                  {/* Tier badges */}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {(Object.keys(tierConfig) as TierKey[]).map((tier) => {
                      const included = activity.tiers.includes(tier)
                      return (
                        <span
                          key={tier}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium",
                            included
                              ? cn(tierConfig[tier].bgActive, tierConfig[tier].textActive, "border-transparent")
                              : "border-border text-muted-foreground/40"
                          )}
                        >
                          {included && <Check className="h-3 w-3" />}
                          {tierConfig[tier].name}
                        </span>
                      )
                    })}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Tier Breakdown Cards */}
        <div className="mx-auto mt-20 max-w-6xl">
          <h3 className="mb-8 text-center text-xl font-semibold text-foreground">
            Quick comparison
          </h3>
          <div className="grid gap-6 md:grid-cols-3">
            {/* Basic */}
            <div className="rounded-xl border border-border bg-card p-6">
              <div className="mb-4 flex items-center gap-3">
                <div className="rounded-full bg-muted-foreground/20 p-2">
                  <BookOpen className="h-4 w-4 text-muted-foreground" />
                </div>
                <div>
                  <h4 className="font-semibold text-foreground">Basic</h4>
                  <p className="text-xs text-muted-foreground">Content only</p>
                </div>
              </div>
              <ul className="space-y-2">
                {activities.filter(a => a.tiers.includes("basic")).map(a => (
                  <li key={a.title} className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Check className="h-3.5 w-3.5 shrink-0 text-accent" />
                    {a.title}
                  </li>
                ))}
              </ul>
              <div className="mt-4 border-t border-border pt-4">
                <p className="text-xs text-muted-foreground">
                  {activities.filter(a => a.tiers.includes("basic")).length} activities
                </p>
              </div>
            </div>

            {/* Main */}
            <div className="rounded-xl border border-accent bg-card p-6">
              <div className="mb-4 flex items-center gap-3">
                <div className="rounded-full bg-accent p-2">
                  <Users className="h-4 w-4 text-accent-foreground" />
                </div>
                <div>
                  <h4 className="font-semibold text-foreground">Main</h4>
                  <p className="text-xs text-accent">Structure + accountability</p>
                </div>
              </div>
              <ul className="space-y-2">
                {activities.filter(a => a.tiers.includes("main")).map(a => (
                  <li key={a.title} className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Check className="h-3.5 w-3.5 shrink-0 text-accent" />
                    {a.title}
                  </li>
                ))}
              </ul>
              <div className="mt-4 border-t border-border pt-4">
                <p className="text-xs text-muted-foreground">
                  {activities.filter(a => a.tiers.includes("main")).length} activities
                </p>
              </div>
            </div>

            {/* Premium */}
            <div className="rounded-xl border border-foreground bg-card p-6">
              <div className="mb-4 flex items-center gap-3">
                <div className="rounded-full bg-foreground p-2">
                  <Star className="h-4 w-4 text-background" />
                </div>
                <div>
                  <h4 className="font-semibold text-foreground">Premium</h4>
                  <p className="text-xs text-muted-foreground">Courses + career growth</p>
                </div>
              </div>
              <ul className="space-y-2">
                {activities.filter(a => a.tiers.includes("premium")).map(a => (
                  <li key={a.title} className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Check className="h-3.5 w-3.5 shrink-0 text-accent" />
                    {a.title}
                  </li>
                ))}
              </ul>
              <div className="mt-4 border-t border-border pt-4">
                <p className="text-xs text-muted-foreground">
                  All {activities.filter(a => a.tiers.includes("premium")).length} activities
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
