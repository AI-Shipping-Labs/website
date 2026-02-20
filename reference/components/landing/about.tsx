import { Hammer, Rocket, Users, Brain } from "lucide-react"

const features = [
  {
    icon: Hammer,
    title: "Learning by doing",
    description: "No passive consumption. Every activity is designed around building, shipping, and getting feedback on real work.",
  },
  {
    icon: Rocket,
    title: "Production-ready",
    description: "Focus on what actually works in production. Move from prototypes to reliable systems with battle-tested patterns.",
  },
  {
    icon: Users,
    title: "Build together",
    description: "Work alongside other practitioners. Hackathons, projects, and group problem-solving instead of isolated learning.",
  },
  {
    icon: Brain,
    title: "Calibrate your judgment",
    description: "Develop better instincts through peer feedback, expert guidance, and exposure to real-world decision-making patterns.",
  },
]

export function About() {
  return (
    <section id="about" className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-medium uppercase tracking-widest text-accent">Philosophy</p>
          <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
            Learn by building, together
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground">
            Designed for motivated learners who prefer learning by doing. Get clear frameworks, direction, and community support to make consistent progress on your projects.
          </p>
        </div>
        
        <div className="mx-auto mt-16 grid max-w-5xl gap-8 sm:grid-cols-2">
          {features.map((feature) => (
            <div
              key={feature.title}
              className="group rounded-lg border border-border bg-card p-8 transition-colors hover:border-accent/50"
            >
              <div className="mb-4 inline-flex rounded-lg bg-secondary p-3">
                <feature.icon className="h-6 w-6 text-accent" />
              </div>
              <h3 className="text-lg font-semibold text-foreground">{feature.title}</h3>
              <p className="mt-2 text-muted-foreground">{feature.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
