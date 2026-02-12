import Link from "next/link"
import { ArrowLeft, ExternalLink, Youtube, Linkedin, BookOpen, GraduationCap, Trophy, Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"

export const metadata = {
  title: "About Alexey Grigorev | AI Engineering Lab",
  description: "Meet Alexey Grigorev Grigorev - Software engineer and ML practitioner with 15+ years of experience. Founder of DataTalks.Club and creator of the Zoomcamp series.",
}

const highlights = [
  {
    icon: Users,
    label: "100,000+",
    description: "Learners reached globally through Zoomcamps",
  },
  {
    icon: GraduationCap,
    label: "15+ Years",
    description: "Experience in software engineering",
  },
  {
    icon: BookOpen,
    label: "Author",
    description: "Machine Learning Bookcamp",
  },
  {
    icon: Trophy,
    label: "Kaggle Master",
    description: "Top rankings in international competitions",
  },
]

const links = [
  {
    label: "DataTalks.Club",
    href: "https://datatalks.club",
    description: "Community for data practitioners",
  },
  {
    label: "Zoomcamps Overview",
    href: "https://datatalks.club/blog/guide-to-free-online-courses-at-datatalks-club.html",
    description: "Free, code-first learning programs",
  },
  {
    label: "AI Engineering Buildcamp",
    href: "https://maven.com/Alexey Grigorev-grigorev/from-rag-to-agents",
    description: "From RAG to Agents course",
  },
  {
    label: "CV & Portfolio",
    href: "https://Alexey Grigorevgrigorev.com/cv",
    description: "Full background and experience",
  },
]

export default function AboutPage() {
  return (
    <main className="min-h-screen">
      <Header />

      <section className="px-6 pt-32 pb-16 lg:px-8 lg:pt-40 lg:pb-24">
        <div className="mx-auto max-w-4xl">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground mb-8"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>

          <div className="flex flex-col gap-8 lg:flex-row lg:gap-16">
            <div className="flex-shrink-0">
              <div className="h-48 w-48 rounded-2xl bg-secondary flex items-center justify-center">
                <span className="text-6xl font-bold text-muted-foreground">AG</span>
              </div>
              <div className="mt-6 flex gap-3">
                <a
                  href="https://youtube.com/@datatalksclub"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                  aria-label="YouTube"
                >
                  <Youtube className="h-5 w-5" />
                </a>
                <a
                  href="https://linkedin.com/in/agrigorev"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                  aria-label="LinkedIn"
                >
                  <Linkedin className="h-5 w-5" />
                </a>
                <a
                  href="https://alexeyondata.substack.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                  aria-label="Substack"
                >
                  <BookOpen className="h-5 w-5" />
                </a>
              </div>
            </div>

            <div className="flex-1">
              <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Alexey Grigorev Grigorev</h1>
              <p className="mt-2 text-lg text-accent">Software Engineer & ML Practitioner</p>

              <div className="mt-6 space-y-4 text-muted-foreground leading-relaxed">
                <p>
                  Software engineer and machine learning practitioner with 15 years of experience in software engineering
                  and 12+ years in machine learning.
                </p>
                <p>
                  <strong className="text-foreground">Founder of DataTalks.Club</strong>, a community focused on practical data, ML, and AI engineering.
                  Through the Slack community and open programs, DataTalks.Club connects tens of thousands of practitioners worldwide.
                </p>
                <p>
                  <strong className="text-foreground">Creator of the Zoomcamp series</strong> — free, code-first programs covering machine learning,
                  data engineering, MLOps, LLMs, and AI developer tools. These programs emphasize hands-on learning and
                  real-world systems and have reached 100,000+ learners globally.
                </p>
                <p>
                  My work centers on practical, production-grade ML and AI systems. I focus on how to move from early
                  prototypes to reliable systems in production, including problem formulation, data pipelines, modeling,
                  evaluation, deployment, and long-term operation.
                </p>
              </div>
            </div>
          </div>

          <div className="mt-16 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {highlights.map((item) => (
              <div key={item.label} className="rounded-xl border border-border bg-card p-6">
                <item.icon className="h-6 w-6 text-accent" />
                <p className="mt-4 text-2xl font-bold">{item.label}</p>
                <p className="mt-1 text-sm text-muted-foreground">{item.description}</p>
              </div>
            ))}
          </div>

          <div className="mt-16">
            <h2 className="text-xl font-semibold">Background</h2>
            <div className="mt-6 space-y-4 text-muted-foreground leading-relaxed">
              <p>
                Previously <strong className="text-foreground">Senior and Principal Data Scientist at OLX Group</strong>,
                where I led the development of a company-wide ML platform and worked with cross-functional ML and MLOps teams.
                Earlier roles included large-scale ML infrastructure, search, ads, and user modeling.
              </p>
              <p>
                Author of technical books, including <strong className="text-foreground">Machine Learning Bookcamp</strong>.
                Former Kaggle Master with top rankings in international competitions such as the NIPS'17 Criteo Challenge and WSDM Cup 2017.
              </p>
            </div>

            <h3 className="mt-8 text-lg font-medium">Areas of Focus</h3>
            <div className="mt-4 flex flex-wrap gap-2">
              {["AI Engineering", "Agentic Systems", "Production ML", "MLOps", "Open-Source Education", "Learning by Building"].map((tag) => (
                <span key={tag} className="rounded-full border border-border bg-secondary px-3 py-1 text-sm">
                  {tag}
                </span>
              ))}
            </div>
          </div>

          <div className="mt-16">
            <h2 className="text-xl font-semibold">Learn More</h2>
            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              {links.map((link) => (
                <a
                  key={link.label}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex items-start gap-4 rounded-xl border border-border bg-card p-5 transition-colors hover:border-accent"
                >
                  <div className="flex-1">
                    <p className="font-medium group-hover:text-accent transition-colors">{link.label}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{link.description}</p>
                  </div>
                  <ExternalLink className="h-4 w-4 text-muted-foreground group-hover:text-accent transition-colors flex-shrink-0" />
                </a>
              ))}
            </div>
          </div>

          <div className="mt-16 rounded-2xl border border-accent/30 bg-accent/5 p-8 text-center">
            <h2 className="text-xl font-semibold">Why AI Engineering Lab?</h2>
            <p className="mt-4 text-muted-foreground max-w-2xl mx-auto leading-relaxed">
              After years of building communities and teaching at scale, I wanted to create something more focused.
              A smaller group where the conversations go deeper, where I can provide real feedback on your work,
              and where we can think through hard problems together. Not more content — better calibration.
            </p>
            <Button asChild size="lg" className="mt-8 bg-accent text-accent-foreground hover:bg-accent/90">
              <Link href="/#tiers">View Membership Tiers</Link>
            </Button>
          </div>
        </div>
      </section>

      <Footer />
    </main>
  )
}
