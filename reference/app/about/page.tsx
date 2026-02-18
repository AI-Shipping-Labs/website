import Link from "next/link"
import Image from "next/image"
import { ArrowLeft, ExternalLink, Youtube, Linkedin, BookOpen, GraduationCap, Trophy, Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"

export const metadata = {
  title: "About | AI Shipping Labs",
  description: "Learn about AI Shipping Labs community and its founders Alexey Grigorev and Valeriia Kuka.",
}

const alexeyHighlights = [
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

const alexeyLinks = [
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
    href: "https://maven.com/alexey-grigorev/from-rag-to-agents",
    description: "From RAG to Agents course",
  },
  {
    label: "CV & Portfolio",
    href: "https://alexeygrigorev.com/cv",
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

          {/* Community Introduction */}
          <div className="mb-16">
            <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">About AI Shipping Labs</h1>
            <div className="mt-6 space-y-4 text-muted-foreground leading-relaxed">
              <p>
                AI Shipping Labs is designed for action-oriented builders interested in AI engineering and AI tools 
                who want to turn ideas into real projects. Whether you're learning Python or currently working as an 
                ML engineer, this community gives you the structure, focus, and accountability to ship practical AI products.
              </p>
            </div>
          </div>

          {/* Founders Section */}
          <div className="mb-16">
            <h2 className="text-2xl font-semibold mb-8">Founders</h2>
            
            {/* Alexey Grigorev */}
            <div className="mb-16 flex flex-col gap-8 lg:flex-row lg:gap-16">
              <div className="flex-shrink-0">
                <div className="h-48 w-48 rounded-2xl overflow-hidden">
                  <Image
                    src="/alexey.png"
                    alt="Alexey Grigorev"
                    width={192}
                    height={192}
                    className="h-full w-full object-cover"
                  />
                </div>
                <div className="mt-6 flex gap-3">
                  <a
                    href="https://linkedin.com/in/agrigorev"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                    aria-label="LinkedIn"
                  >
                    <Linkedin className="h-5 w-5" />
                  </a>
                </div>
              </div>

              <div className="flex-1">
                <h3 className="text-2xl font-bold tracking-tight">Alexey Grigorev</h3>
                <p className="mt-2 text-lg text-accent">Co-founder & ML Engineer</p>

                <div className="mt-6 space-y-4 text-muted-foreground leading-relaxed">
                  <p>
                    Software engineer and machine learning practitioner with 15+ years of experience building production ML systems.
                    I focus on practical, production-grade ML and AI systems, from early prototypes to reliable systems in production.
                  </p>
                  <p>
                    I'm the founder of DataTalks.Club, a free community that connects tens of thousands 
                    of practitioners worldwide, and the creator of the Zoomcamp series, free, code-first 
                    programs that have reached 100,000+ learners globally.
                  </p>
                  <p>
                    At AI Shipping Labs, I'm building the kind of environment that would have accelerated my own career growth. 
                    After years of teaching at scale, I wanted something more focused: a space for action-oriented builders who 
                    want to turn AI ideas into real projects. The community gives members the structure, accountability, and peer 
                    support to ship practical AI products consistently, even alongside their main jobs.
                  </p>
                </div>
              </div>
            </div>

            {/* Valeriia Kuka */}
            <div className="flex flex-col gap-8 lg:flex-row lg:gap-16">
              <div className="flex-shrink-0">
                <div className="h-48 w-48 rounded-2xl overflow-hidden">
                  <Image
                    src="/valeriia.png"
                    alt="Valeriia Kuka"
                    width={192}
                    height={192}
                    className="h-full w-full object-cover"
                  />
                </div>
                <div className="mt-6 flex gap-3">
                  <a
                    href="https://linkedin.com/in/valeriia-kuka"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                    aria-label="LinkedIn"
                  >
                    <Linkedin className="h-5 w-5" />
                  </a>
                </div>
              </div>

              <div className="flex-1">
                <h3 className="text-2xl font-bold tracking-tight">Valeriia Kuka</h3>
                <p className="mt-2 text-lg text-accent">Co-founder & Content Strategist</p>

                <div className="mt-6 space-y-4 text-muted-foreground leading-relaxed">
                  <p>
                    Content strategist and technical writer specializing in AI/ML education. I focus on making complex 
                    technical concepts accessible and helping builders learn through clear, practical content.
                  </p>
                  <p>
                    At AI Shipping Labs, I work alongside Alexey to shape the community's content strategy and member experience. 
                    I ensure that motivated learners have the resources, frameworks, and clear direction they need to make 
                    consistent progress on their AI projects. My goal is to help builders bridge the gap from ideas to 
                    shipped products by providing structure and removing friction from the learning-by-doing process.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Why AI Shipping Labs */}
          <div className="mt-16 rounded-2xl border border-accent/30 bg-accent/5 p-8 text-center">
            <h2 className="text-xl font-semibold">Why AI Shipping Labs?</h2>
            <p className="mt-4 text-muted-foreground max-w-2xl mx-auto leading-relaxed">
              If you have AI project ideas but lack structure, focus, and accountability, this community is for you. 
              Get clear frameworks, direction, and gentle external pressure to make consistent progress. 
              Build alongside motivated practitioners who turn ideas into real projects and contribute back to the ecosystem.
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
