import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { Button } from "@/components/ui/button"

export function Hero() {
  return (
    <section className="relative min-h-screen overflow-hidden pt-24">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-secondary via-background to-background" />
      
      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:px-8 lg:py-32">
        <div className="mx-auto max-w-3xl text-center">
          <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-secondary/50 px-4 py-1.5 text-sm text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Invite-only community
          </p>
          
          <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl">
            Where practitioners sharpen
            <span className="mt-2 block text-accent">their technical judgment</span>
          </h1>
          
          <p className="mx-auto mt-8 max-w-2xl text-pretty text-lg leading-relaxed text-muted-foreground">
            A closed community for AI, data, and engineering practitioners who want signal over noise. 
            Led by Alexey Grigorev. Built for calibration, not content consumption.
          </p>
          
          <div className="mt-12 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Button asChild size="lg" className="w-full bg-accent text-accent-foreground hover:bg-accent/90 sm:w-auto">
              <Link href="/#newsletter">
                Subscribe for updates
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
            <Button 
              variant="outline" 
              size="lg" 
              className="w-full border-border text-foreground hover:bg-secondary sm:w-auto bg-transparent"
            >
              View Membership Tiers
            </Button>
          </div>
          
          <div className="mt-16 grid grid-cols-3 gap-8 border-t border-border pt-8">
            <div>
              <p className="text-2xl font-semibold text-foreground">3</p>
              <p className="mt-1 text-sm text-muted-foreground">Membership tiers</p>
            </div>
            <div>
              <p className="text-2xl font-semibold text-foreground">AI + Data</p>
              <p className="mt-1 text-sm text-muted-foreground">Focus areas</p>
            </div>
            <div>
              <p className="text-2xl font-semibold text-foreground">Limited</p>
              <p className="mt-1 text-sm text-muted-foreground">Seats available</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
